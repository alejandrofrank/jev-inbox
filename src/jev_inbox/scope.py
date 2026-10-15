"""Persist the explicitly selected recent messages and the future-arrival boundary."""

import json
import time


def configure_scope(gmail, store, latest, *, clock=time.time):
    if latest < 1:
        raise ValueError("latest must be positive")
    with store.transaction():
        account, baseline = gmail.profile()
        if store.get("account") not in {None, account}:
            raise RuntimeError("This database belongs to a different Gmail account.")
        if store.get("scope_started_ms"):
            if store.get("scope_latest") != str(latest):
                raise RuntimeError("A different recent-mail scope is already configured.")
            return len(json.loads(store.get("scope_initial_ids")))
        if (
            store.get("history_id")
            or store.get("backfill_history_id")
            or store.db.execute("SELECT COUNT(*) FROM processed").fetchone()[0]
        ):
            raise RuntimeError("Recent-mail scope needs a fresh model database.")
        # Capture history first, then define 'now'. Messages arriving during selection
        # remain eligible through history, even if selecting the initial set takes time.
        started_ms = int(clock() * 1000)
        ids = gmail.latest_inbox_ids(latest, started_ms)
        store.set("account", account)
        store.set("history_id", baseline)
        store.set("scope_started_ms", started_ms)
        store.set("scope_latest", latest)
        store.set("scope_initial_ids", json.dumps(ids))
        store.set("scope_pending", "1")
        return len(ids)


def configure_days_scope(gmail, store, days, *, clock=time.time):
    """Freeze a one-time date backfill, followed by future arrivals only."""
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    with store.transaction():
        account, baseline = gmail.profile()
        if store.get("account") not in {None, account}:
            raise RuntimeError("This database belongs to a different Gmail account.")
        if store.get("scope_started_ms"):
            if store.get("scope_days") != str(days):
                raise RuntimeError("A different recent-mail scope is already configured.")
            return len(json.loads(store.get("scope_initial_ids")))
        if (
            store.get("history_id")
            or store.get("backfill_history_id")
            or store.db.execute("SELECT COUNT(*) FROM processed").fetchone()[0]
        ):
            raise RuntimeError("Recent-mail scope needs a fresh model database.")
        started_ms = int(clock() * 1000)
        cutoff_ms = max(0, started_ms - days * 86400000)
        query = f"after:{max(0, cutoff_ms // 1000 - 1)} before:{started_ms // 1000 + 1}"
        ids = list(dict.fromkeys(gmail.inbox_ids(query=query)))
        # Boundary seconds may overlap. Sync checks the exact timestamp using
        # metadata before fetching a body and again after the full fetch.
        store.set("account", account)
        store.set("history_id", baseline)
        store.set("scope_started_ms", started_ms)
        store.set("scope_cutoff_ms", cutoff_ms)
        store.set("scope_days", days)
        store.set("scope_initial_ids", json.dumps(ids))
        store.set("scope_pending", "1")
        return len(ids)
