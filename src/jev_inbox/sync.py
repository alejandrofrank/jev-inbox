"""Incremental INBOX sync with safe replay after any partial failure."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import chain

from .classifier import MODEL, POLICY
from .gmail import StaleHistory
from .labels import JOB_LABEL


@dataclass
class SyncResult:
    mode: str = "incremental"
    inspected: int = 0
    matched: int = 0
    ignored: int = 0
    uncertain: int = 0
    skipped: int = 0
    dry_run: bool = False
    complete: bool = True
    model: str = MODEL

    def to_dict(self):
        return asdict(self)


def synchronize(
    gmail,
    store,
    classifier,
    *,
    dry_run=False,
    label_only=False,
    max_messages=None,
    require_scope=False,
):
    if max_messages is not None and max_messages < 1:
        raise ValueError("max_messages must be positive")
    result = SyncResult(dry_run=dry_run)
    with store.transaction(dry_run=dry_run):
        started = store.get("scope_started_ms")
        if require_scope and not started:
            raise RuntimeError("Recent-mail scope is missing; run scope --latest first.")
        initial_ids = json.loads(store.get("scope_initial_ids")) if started else []
        initial_set = set(initial_ids)
        pending = started and store.get("scope_pending") == "1"

        def recovery_ids():
            if started:
                query = f"after:{max(0, int(started) // 1000 - 1)}"
                return chain(initial_ids, gmail.inbox_ids(query=query))
            return gmail.inbox_ids()

        if store.get("classifier_policy") not in {None, POLICY}:
            raise RuntimeError("This database uses a different classifier policy.")
        if store.get("classifier_policy") is None:
            if store.db.execute("SELECT COUNT(*) FROM processed").fetchone()[0]:
                raise RuntimeError("Legacy rule receipts require a separate Jev database.")
            store.set("classifier_policy", POLICY)
        account, baseline = gmail.profile()
        if store.get("account") not in {None, account}:
            raise RuntimeError("This database belongs to a different Gmail account.")
        cursor = store.get("history_id")
        backfill_baseline = store.get("backfill_history_id")
        if pending:
            result.mode = "initial_recent"
            ids, next_cursor = initial_ids, cursor
        elif backfill_baseline:
            result.mode = "recovery" if cursor else "initial"
            ids, next_cursor = recovery_ids(), backfill_baseline
        elif cursor:
            try:
                ids, next_cursor = gmail.changes(cursor)
            except StaleHistory:
                result.mode = "recovery"
                ids, next_cursor = recovery_ids(), baseline
        else:
            result.mode = "initial"
            ids, next_cursor = recovery_ids(), baseline
        # A pre-list baseline makes mail arriving during a full scan eligible next run.
        visited = set()
        for message_id in ids:
            if message_id in visited or store.seen(message_id):
                result.skipped += 1
                continue
            visited.add(message_id)
            if max_messages is not None and result.inspected >= max_messages:
                result.complete = False
                break
            if started and message_id not in initial_set:
                received = gmail.inbox_received_ms(message_id)
                if received is None or received < int(started):
                    result.skipped += 1
                    continue
            message = gmail.inbox_message(message_id)
            if message is None:
                result.skipped += 1
                continue  # A later move into INBOX must still be eligible.
            if started and message_id not in initial_set and message.received_ms < int(started):
                result.skipped += 1
                continue
            result.inspected += 1
            evaluation = classifier(message.subject, message.body)
            decision = evaluation.decision
            if decision:
                labels = (JOB_LABEL,) if label_only else decision.labels
                if not dry_run and not gmail.add_labels(message.id, labels):
                    result.skipped += 1
                    continue
                result.matched += 1
            elif evaluation.outcome == "uncertain":
                result.uncertain += 1
            else:
                result.ignored += 1
            store.record(message, decision, track_application=not label_only)
            store.record_evaluation(message.id, evaluation)
        store.set("account", account)
        if result.complete:
            store.set("history_id", next_cursor)
            store.delete("backfill_history_id")
            if pending:
                store.set("scope_pending", "0")
        elif result.mode not in {"incremental", "initial_recent"}:
            store.set("backfill_history_id", next_cursor)
        store.set("last_sync", datetime.now(UTC).isoformat())
    return result
