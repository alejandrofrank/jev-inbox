"""Local summaries, idempotency receipts, and an atomic history checkpoint."""

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS processed (
    message_id TEXT PRIMARY KEY, disposition TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_decisions (
    message_id TEXT PRIMARY KEY, model TEXT NOT NULL, category TEXT NOT NULL,
    probability REAL NOT NULL, confidence REAL NOT NULL, outcome TEXT NOT NULL,
    correspondence_probability REAL, alert_probability REAL, blocked_sender INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
    subject TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
    received_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS events_thread_date ON events(thread_id, received_ms, message_id);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY, subject TEXT NOT NULL, status TEXT NOT NULL,
    first_received_ms INTEGER NOT NULL, last_received_ms INTEGER NOT NULL,
    last_message_id TEXT NOT NULL, message_count INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=1, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        # Keep old evidence readable; old policies still cannot be used for syncing.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(model_decisions)")}
        for name, kind in (
            ("correspondence_probability", "REAL"),
            ("alert_probability", "REAL"),
            ("blocked_sender", "INTEGER"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE model_decisions ADD COLUMN {name} {kind}")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self, *, dry_run=False):
        # One synchronizer per database. WAL keeps API readers available during network calls.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("ROLLBACK" if dry_run else "COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def get(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        self.db.execute(
            "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def seen(self, message_id):
        return (
            self.db.execute("SELECT 1 FROM processed WHERE message_id=?", (message_id,)).fetchone()
            is not None
        )

    def delete(self, key):
        self.db.execute("DELETE FROM state WHERE key=?", (key,))

    def record_evaluation(self, message_id, evaluation):
        self.db.execute(
            "INSERT OR IGNORE INTO model_decisions "
            "(message_id,model,category,probability,confidence,outcome,"
            "correspondence_probability,alert_probability,blocked_sender) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                evaluation.model,
                evaluation.category,
                evaluation.probability,
                evaluation.confidence,
                evaluation.outcome,
                evaluation.correspondence_probability,
                evaluation.alert_probability,
                int(evaluation.blocked_sender),
            ),
        )

    def record(self, message, decision, *, track_application=True):
        disposition = decision.status if decision else "ignored"
        self.db.execute("INSERT OR IGNORE INTO processed VALUES (?,?)", (message.id, disposition))
        if not track_application or not decision or not decision.application:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)",
            (
                message.id,
                message.thread_id,
                message.subject,
                decision.status,
                decision.reason,
                message.received_ms,
            ),
        )
        # Gmail listing order is not guaranteed; derive state from event time, never visit order.
        latest = self.db.execute(
            "SELECT * FROM events WHERE thread_id=? ORDER BY received_ms DESC, message_id DESC "
            "LIMIT 1",
            (message.thread_id,),
        ).fetchone()
        substantive = self.db.execute(
            "SELECT status FROM events WHERE thread_id=? AND status!='update' "
            "ORDER BY received_ms DESC, message_id DESC LIMIT 1",
            (message.thread_id,),
        ).fetchone()
        totals = self.db.execute(
            "SELECT MIN(received_ms), COUNT(*) FROM events WHERE thread_id=?", (message.thread_id,)
        ).fetchone()
        self.db.execute(
            "INSERT INTO applications VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "subject=excluded.subject, status=excluded.status, "
            "first_received_ms=excluded.first_received_ms, "
            "last_received_ms=excluded.last_received_ms, "
            "last_message_id=excluded.last_message_id, message_count=excluded.message_count",
            (
                message.thread_id,
                latest["subject"],
                substantive[0] if substantive else "update",
                totals[0],
                latest["received_ms"],
                latest["message_id"],
                totals[1],
            ),
        )


@contextmanager
def read_database(path):
    # mode=ro fails if the database is missing and cannot create or modify it.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()
