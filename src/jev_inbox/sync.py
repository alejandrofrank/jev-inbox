"""Incremental INBOX sync with safe replay after any partial failure."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .classifier import classify
from .gmail import StaleHistory


@dataclass
class SyncResult:
    mode: str = "incremental"
    inspected: int = 0
    matched: int = 0
    ignored: int = 0
    skipped: int = 0
    dry_run: bool = False

    def to_dict(self):
        return asdict(self)


def synchronize(gmail, store, *, dry_run=False):
    result = SyncResult(dry_run=dry_run)
    with store.transaction(dry_run=dry_run):
        account, baseline = gmail.profile()
        if store.get("account") not in {None, account}:
            raise RuntimeError("This database belongs to a different Gmail account.")
        cursor = store.get("history_id")
        if cursor:
            try:
                ids, next_cursor = gmail.changes(cursor)
            except StaleHistory:
                result.mode = "recovery"
                ids, next_cursor = gmail.inbox_ids(), baseline
        else:
            result.mode = "initial"
            ids, next_cursor = gmail.inbox_ids(), baseline
        # A pre-list baseline makes mail arriving during a full scan eligible next run.
        visited = set()
        for message_id in ids:
            if message_id in visited or store.seen(message_id):
                result.skipped += 1
                continue
            visited.add(message_id)
            message = gmail.inbox_message(message_id)
            if message is None:
                result.skipped += 1
                continue  # A later move into INBOX must still be eligible.
            result.inspected += 1
            decision = classify(message.subject, message.body)
            if decision:
                if not dry_run and not gmail.add_labels(message.id, decision.labels):
                    result.skipped += 1
                    continue
                result.matched += 1
            else:
                result.ignored += 1
            store.record(message, decision)
        store.set("account", account)
        store.set("history_id", next_cursor)
        store.set("last_sync", datetime.now(UTC).isoformat())
    return result
