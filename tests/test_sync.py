import sqlite3

import pytest

from jev_inbox.classifier import classify
from jev_inbox.gmail import Message, StaleHistory
from jev_inbox.store import Store, read_database
from jev_inbox.sync import synchronize


def message(id="m1", thread="t1", subject="Application received", body="", date=1000):
    return Message(id, thread, subject, body, date)


class FakeGmail:
    def __init__(self, messages):
        self.messages = {m.id: m for m in messages}
        self.ids = list(self.messages)
        self.modified = []
        self.fetched = []
        self.cursor = "20"
        self.account = "synthetic-account"
        self.baseline = "10"
        self.stale = False
        self.fail_on = None
        self.archived = set()
        self.full_scans = 0

    def profile(self):
        return self.account, self.baseline

    def inbox_ids(self, *, query=None):
        self.full_scans += 1
        self.last_query = query
        return self.ids

    def changes(self, cursor):
        assert cursor
        if self.stale:
            raise StaleHistory
        return self.ids, self.cursor

    def inbox_message(self, id):
        self.fetched.append(id)
        return self.messages.get(id)

    def add_labels(self, id, labels):
        if id == self.fail_on:
            raise OSError("synthetic failure")
        if id in self.archived:
            return False
        self.modified.append((id, labels))
        return True


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    yield db
    db.close()


def test_initial_only_changes_jobs_and_replay_is_idempotent(store):
    gmail = FakeGmail([message(), message("m2", subject="Dinner on Friday")])
    result = synchronize(gmail, store)
    assert (result.matched, result.ignored, result.mode) == (1, 1, "initial")
    assert gmail.modified == [("m1", ("JEV", "JEV/Applied"))]
    assert store.get("history_id") == "10"
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    synchronize(gmail, store)
    assert gmail.full_scans == 1
    assert len(gmail.fetched) == 2
    assert len(gmail.modified) == 1
    assert store.get("history_id") == "20"


def test_failed_batch_rolls_back_checkpoint_and_can_retry(store):
    gmail = FakeGmail([message(), message("m2", "t2")])
    gmail.fail_on = "m2"
    with pytest.raises(OSError):
        synchronize(gmail, store)
    assert store.get("history_id") is None
    assert not store.seen("m1")
    gmail.fail_on = None
    synchronize(gmail, store)
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 2


def test_dry_run_does_not_write_labels_receipts_or_cursor(store):
    gmail = FakeGmail([message()])
    result = synchronize(gmail, store, dry_run=True)
    assert result.matched == 1
    assert not gmail.modified and not store.seen("m1")
    assert store.get("history_id") is None


def test_expired_cursor_recovers_current_inbox_without_duplicates(store):
    gmail = FakeGmail([message()])
    synchronize(gmail, store)
    gmail.messages["m2"] = message("m2", "t2")
    gmail.ids.append("m2")
    gmail.stale = True
    assert synchronize(gmail, store).mode == "recovery"
    assert [m[0] for m in gmail.modified] == ["m1", "m2"]


def test_missing_or_archived_messages_remain_eligible_when_restored(store):
    gmail = FakeGmail([message()])
    gmail.archived.add("m1")
    gmail.ids.append("missing")
    synchronize(gmail, store)
    assert not store.seen("m1") and not store.seen("missing")
    gmail.archived.clear()
    synchronize(gmail, store)
    assert store.seen("m1")


def test_wrong_mailbox_cannot_reuse_database(store):
    gmail = FakeGmail([message()])
    synchronize(gmail, store)
    gmail.account = "another-synthetic-account"
    with pytest.raises(RuntimeError, match="different Gmail account"):
        synchronize(gmail, store)


def test_out_of_order_updates_preserve_latest_substantive_status(store):
    messages = [
        message("m3", subject="Update on your application", date=3000),
        message("m2", subject="Your application: interview invitation", date=2000),
        message("m1", date=1000),
    ]
    synchronize(FakeGmail(messages), store)
    row = store.db.execute("SELECT * FROM applications").fetchone()
    assert row["status"] == "interview"
    assert row["message_count"] == 3
    assert row["first_received_ms"] == 1000 and row["last_received_ms"] == 3000


def test_alerts_are_labeled_but_not_application_records(store):
    gmail = FakeGmail([message(subject="Job alert: Python developer")])
    synchronize(gmail, store)
    assert gmail.modified == [("m1", ("JEV", "JEV/JobAlert"))]
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_raw_body_is_not_persisted(store):
    item = message(body="PRIVATE_SYNTHETIC_BODY_MARKER")
    store.record(item, classify(item.subject, item.body))
    assert "PRIVATE_SYNTHETIC_BODY_MARKER" not in "\n".join(store.db.iterdump())


def test_label_only_creates_one_folder_and_persists_no_mail_text(store):
    gmail = FakeGmail(
        [
            message(subject="Application received", body="PRIVATE_BODY_MARKER"),
            message("m2", subject="Job alert: Python developer"),
            message("m3", subject="Dinner on Friday"),
        ]
    )
    result = synchronize(gmail, store, label_only=True)
    assert result.matched == 2
    assert gmail.modified == [("m1", ("JEV",)), ("m2", ("JEV",))]
    assert gmail.last_query is not None
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert store.seen("m1") and store.seen("m2") and store.seen("m3")
    assert "PRIVATE_BODY_MARKER" not in "\n".join(store.db.iterdump())
    assert "Application received" not in "\n".join(store.db.iterdump())
    synchronize(gmail, store, label_only=True)
    assert len(gmail.modified) == 2


def test_partial_initial_scan_retains_original_baseline_and_resumes(store):
    gmail = FakeGmail([message(), message("m2", "t2"), message("m3", "t3")])
    first = synchronize(gmail, store, label_only=True, max_messages=1)
    assert not first.complete and store.seen("m1")
    assert store.get("history_id") is None
    assert store.get("backfill_history_id") == "10"
    gmail.baseline = "50"
    assert not synchronize(gmail, store, label_only=True, max_messages=1).complete
    assert synchronize(gmail, store, label_only=True, max_messages=1).complete
    assert store.get("history_id") == "10"
    assert store.get("backfill_history_id") is None
    assert len(gmail.modified) == 3


def test_partial_incremental_scan_does_not_skip_unfinished_history(store):
    gmail = FakeGmail([message()])
    synchronize(gmail, store)
    gmail.messages.update({"m2": message("m2", "t2"), "m3": message("m3", "t3")})
    gmail.ids = ["m2", "m3"]
    assert not synchronize(gmail, store, max_messages=1).complete
    assert store.get("history_id") == "10"
    assert synchronize(gmail, store, max_messages=1).complete
    assert store.get("history_id") == "20"
    assert len(gmail.modified) == 3


def test_recovery_can_resume_in_batches(store):
    gmail = FakeGmail([message()])
    synchronize(gmail, store)
    gmail.messages.update({"m2": message("m2", "t2"), "m3": message("m3", "t3")})
    gmail.ids = ["m1", "m2", "m3"]
    gmail.stale, gmail.baseline = True, "50"
    assert not synchronize(gmail, store, max_messages=1).complete
    assert store.get("history_id") == "10"
    gmail.baseline = "60"
    assert synchronize(gmail, store, max_messages=1).complete
    assert store.get("history_id") == "50"


def test_partial_dry_run_does_not_save_progress(store):
    gmail = FakeGmail([message(), message("m2", "t2")])
    assert not synchronize(gmail, store, dry_run=True, max_messages=1).complete
    assert store.get("backfill_history_id") is None
    assert not store.seen("m1")


def test_writers_are_serialized_and_read_connection_cannot_write(tmp_path):
    path = tmp_path / "test.sqlite3"
    first, second = Store(path), Store(path)
    try:
        with first.transaction():
            with pytest.raises(sqlite3.OperationalError, match="locked"), second.transaction():
                pass
            with read_database(path) as reader:
                assert reader.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
                with pytest.raises(sqlite3.OperationalError):
                    reader.execute("DELETE FROM applications")
    finally:
        first.close()
        second.close()
