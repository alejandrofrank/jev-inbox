import sqlite3

import pytest

from jev_inbox.classifier import MODEL, Decision, Evaluation, JevError
from jev_inbox.gmail import Message, StaleHistory
from jev_inbox.scope import configure_days_scope, configure_scope
from jev_inbox.store import Store, read_database
from jev_inbox.sync import synchronize as run_sync


def synthetic_classifier(subject, body, **sender):
    # Fixed test fixtures, not an alternate production classifier.
    categories = {
        "Application received": "applied",
        "Job alert: Python developer": "job_alert",
        "Update on your application": "update",
        "Your application: interview invitation": "interview",
        "Project handoff": "work",
    }
    category = categories.get(subject, "unrelated")
    return Evaluation(
        MODEL,
        category,
        0.99,
        0.98,
        0.01 if category in {"unrelated", "job_alert"} else 0.99,
        0.99 if category == "job_alert" else 0.01,
        non_job_probability=0.99 if category == "work" else 0.01,
    )


def synchronize(gmail, store, **kwargs):
    return run_sync(gmail, store, synthetic_classifier, **kwargs)


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
        self.metadata_read = []

    def latest_inbox_ids(self, limit, before_ms):
        return [
            m.id
            for m in sorted(
                self.messages.values(), key=lambda item: (item.received_ms, item.id), reverse=True
            )
            if m.received_ms < before_ms
        ][:limit]

    def inbox_received_ms(self, id):
        self.metadata_read.append(id)
        item = self.messages.get(id)
        return item.received_ms if item else None

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

    def inbox_message(self, id, *, minimum_received_ms=0):
        self.metadata_read.append(id)
        item = self.messages.get(id)
        if item is None or item.received_ms < minimum_received_ms:
            return None
        self.fetched.append(id)
        return item

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
    assert gmail.modified == [("m1", ("grok", "grok/Applied"))]
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


def test_alerts_are_excluded_from_folder_and_application_records(store):
    gmail = FakeGmail([message(subject="Job alert: Python developer")])
    synchronize(gmail, store)
    assert gmail.modified == []
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_current_work_is_not_labeled_or_tracked_as_a_job_application(store):
    gmail = FakeGmail([message(subject="Project handoff")])
    synchronize(gmail, store)
    assert gmail.modified == []
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    evidence = store.db.execute("SELECT * FROM model_decisions").fetchone()
    assert evidence["non_job_probability"] == 0.99
    assert evidence["outcome"] == "ignored"


def test_linkedin_message_digest_is_labeled_without_inventing_an_application(store):
    gmail = FakeGmail([message(subject="You have new messages")])
    result = run_sync(
        gmail,
        store,
        lambda subject, body, **sender: Evaluation(
            MODEL,
            "unrelated",
            0.9,
            0.9,
            0.05,
            0.01,
            non_job_probability=0.05,
            linkedin_message_probability=0.98,
            linkedin_sender=True,
        ),
        label_only=True,
    )
    assert result.matched == 1
    assert gmail.modified == [("m1", ("grok",))]
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    evidence = store.db.execute("SELECT * FROM model_decisions").fetchone()
    assert evidence["linkedin_message_probability"] == 0.98
    assert evidence["linkedin_sender"] == 1


def test_raw_body_is_not_persisted(store):
    item = message(body="PRIVATE_SYNTHETIC_BODY_MARKER")
    store.record(item, Decision("applied", "synthetic"))
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
    assert result.matched == 1
    assert gmail.modified == [("m1", ("grok",))]
    assert gmail.last_query is None
    assert store.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert store.seen("m1") and store.seen("m2") and store.seen("m3")
    assert "PRIVATE_BODY_MARKER" not in "\n".join(store.db.iterdump())
    assert "Application received" not in "\n".join(store.db.iterdump())
    synchronize(gmail, store, label_only=True)
    assert len(gmail.modified) == 1


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


def test_model_decides_without_keyword_prefilter_and_records_evidence(store):
    gmail = FakeGmail([message(subject="Next steps", body="Meet the team Tuesday.")])
    calls = []

    def model(subject, body, **sender):
        calls.append((subject, body))
        return Evaluation(MODEL, "interview", 0.97, 0.96, 0.99, 0.01)

    result = run_sync(gmail, store, model, label_only=True)
    assert result.matched == 1 and result.model == MODEL
    assert calls == [("Next steps", "Meet the team Tuesday.")]
    assert gmail.last_query is None and gmail.modified == [("m1", ("grok",))]
    evidence = store.db.execute("SELECT * FROM model_decisions").fetchone()
    assert evidence["model"] == MODEL and evidence["category"] == "interview"
    assert evidence["probability"] == 0.97 and evidence["outcome"] == "matched"
    assert "Meet the team" not in "\n".join(store.db.iterdump())


def test_uncertain_decision_keeps_mail_untouched_and_records_uncertainty(store):
    gmail = FakeGmail([message()])
    result = run_sync(
        gmail,
        store,
        lambda subject, body, **sender: Evaluation(MODEL, "applied", 0.6, 0.5, 0.6, 0.01),
        label_only=True,
    )
    assert result.uncertain == 1 and result.matched == 0 and result.ignored == 0
    assert not gmail.modified
    assert store.db.execute("SELECT outcome FROM model_decisions").fetchone()[0] == "uncertain"


def test_jev_error_does_not_label_using_rules_or_advance_cursor(store):
    gmail = FakeGmail([message()])

    def failing_model(subject, body, **sender):
        raise JevError("Synthetic unavailable model")

    with pytest.raises(JevError):
        run_sync(gmail, store, failing_model, label_only=True)
    assert not gmail.modified and not store.seen("m1")
    assert store.get("history_id") is None and store.get("classifier_policy") is None
    assert store.db.execute("SELECT COUNT(*) FROM model_decisions").fetchone()[0] == 0


def test_legacy_rules_receipts_are_not_mistaken_for_model_decisions(store):
    item = message()
    store.record(item, Decision("applied", "legacy rules"), track_application=False)
    gmail = FakeGmail([item])
    with pytest.raises(RuntimeError, match="Legacy rule receipts"):
        run_sync(gmail, store, synthetic_classifier, label_only=True)
    assert not gmail.fetched and not gmail.modified


def test_model_dry_run_rolls_back_decision_evidence(store):
    gmail = FakeGmail([message()])
    result = synchronize(gmail, store, dry_run=True, label_only=True)
    assert result.matched == 1
    assert store.db.execute("SELECT COUNT(*) FROM model_decisions").fetchone()[0] == 0
    assert not gmail.modified and store.get("classifier_policy") is None


def test_recent_scope_processes_exact_initial_set_then_only_new_arrivals(store):
    gmail = FakeGmail(
        [message("old", date=100), message("recent", date=900), message("newest", date=950)]
    )
    assert configure_scope(gmail, store, 2, clock=lambda: 1) == 2
    # New arrivals during initial processing are not lost.
    gmail.messages["future"] = message("future", date=1100)
    gmail.ids.append("future")
    result = synchronize(gmail, store, label_only=True, require_scope=True)
    assert result.mode == "initial_recent" and result.inspected == 2 and result.complete
    assert gmail.fetched == ["newest", "recent"]
    assert store.get("history_id") == "10"
    result = synchronize(gmail, store, label_only=True, require_scope=True)
    assert result.mode == "incremental" and result.inspected == 1
    assert gmail.fetched == ["newest", "recent", "future"]
    assert not store.seen("old") and gmail.full_scans == 0


def test_recent_scope_resumes_saved_ids_without_expanding_into_old_mail(store):
    gmail = FakeGmail(
        [message("old", date=100), message("recent", date=900), message("newest", date=950)]
    )
    configure_scope(gmail, store, 2, clock=lambda: 1)
    assert not synchronize(gmail, store, max_messages=1).complete
    assert store.get("scope_pending") == "1"
    assert store.get("backfill_history_id") is None
    assert synchronize(gmail, store, max_messages=1).complete
    assert store.get("scope_pending") == "0"
    assert gmail.fetched == ["newest", "recent"]


def test_initial_failure_preserves_scope_and_can_retry_without_selecting_again(store):
    gmail = FakeGmail([message("first", date=950), message("second", date=900)])
    configure_scope(gmail, store, 2, clock=lambda: 1)
    gmail.fail_on = "second"
    with pytest.raises(OSError):
        synchronize(gmail, store)
    assert store.get("scope_pending") == "1" and not store.seen("first")
    gmail.messages["future"] = message("future", date=1100)
    gmail.fail_on = None
    assert synchronize(gmail, store).inspected == 2
    assert not store.seen("future")


def test_recent_scope_stale_history_recovery_stays_within_approved_boundary(store):
    gmail = FakeGmail([message("old", date=100), message("recent", date=900)])
    configure_scope(gmail, store, 1, clock=lambda: 1)
    synchronize(gmail, store)
    gmail.messages["future"] = message("future", date=1100)
    gmail.ids.append("future")
    gmail.stale = True
    result = synchronize(gmail, store, require_scope=True)
    assert result.mode == "recovery" and result.inspected == 1
    assert gmail.last_query == "after:0"
    assert gmail.fetched == ["recent", "future"]
    assert "old" in gmail.metadata_read and not store.seen("old")


def test_scoped_recovery_batches_keep_filter_and_original_checkpoint(store):
    gmail = FakeGmail([message("recent", date=900)])
    configure_scope(gmail, store, 1, clock=lambda: 1)
    synchronize(gmail, store)
    gmail.messages.update(
        {
            "new1": message("new1", date=1100),
            "new2": message("new2", date=1200),
            "old": message("old", date=100),
        }
    )
    gmail.ids = ["old", "new1", "new2"]
    gmail.stale, gmail.baseline = True, "50"
    assert not synchronize(gmail, store, max_messages=1).complete
    gmail.baseline = "70"
    assert synchronize(gmail, store, max_messages=1).complete
    assert store.get("history_id") == "50" and gmail.last_query == "after:0"
    assert "old" not in gmail.fetched


def test_missing_required_scope_fails_before_any_gmail_reads(store):
    gmail = FakeGmail([message()])
    with pytest.raises(RuntimeError, match="scope is missing"):
        synchronize(gmail, store, require_scope=True)
    assert gmail.full_scans == 0 and not gmail.fetched and not gmail.modified


def test_scope_configuration_is_idempotent_and_cannot_silently_expand(store):
    gmail = FakeGmail([message("first", date=900)])
    configure_scope(gmail, store, 1, clock=lambda: 1)
    gmail.messages["second"] = message("second", date=1500)
    assert configure_scope(gmail, store, 1, clock=lambda: 2) == 1
    assert store.get("scope_started_ms") == "1000"
    with pytest.raises(RuntimeError, match="different recent-mail scope"):
        configure_scope(gmail, store, 2, clock=lambda: 2)
    synchronize(gmail, store)
    assert gmail.fetched == ["first"]


def test_existing_unbounded_progress_cannot_be_reinterpreted_as_a_new_scope(store):
    gmail = FakeGmail([message()])
    synchronize(gmail, store)
    with pytest.raises(RuntimeError, match="fresh model database"):
        configure_scope(gmail, store, 100)


def test_date_backfill_rechecks_bounds_before_body_and_resumes_then_follows_history(store):
    now = 100 * 86400000
    cutoff = now - 45 * 86400000
    gmail = FakeGmail(
        [
            message("too_old", date=cutoff - 1),
            message("first", date=cutoff),
            message("second", date=now - 1),
        ]
    )
    configure_days_scope(gmail, store, 45, clock=lambda: now / 1000)
    assert gmail.last_query == f"after:{cutoff // 1000 - 1} before:{now // 1000 + 1}"
    # Frozen selection does not drift when the command is rerun later.
    gmail.messages["new"] = message("new", date=now + 1000)
    gmail.ids.append("new")
    assert configure_days_scope(gmail, store, 45, clock=lambda: now / 1000 + 5000) == 3
    assert not synchronize(gmail, store, label_only=True, max_messages=1).complete
    assert synchronize(gmail, store, label_only=True, max_messages=1).complete
    assert gmail.fetched == ["first", "second"]
    assert synchronize(gmail, store, label_only=True).inspected == 1
    assert gmail.fetched == ["first", "second", "new"]
    gmail.stale = True
    assert synchronize(gmail, store, label_only=True).inspected == 0
    assert "too_old" not in gmail.fetched


def test_date_scope_does_not_silently_replace_another_scope(store):
    gmail = FakeGmail([message(date=900)])
    configure_scope(gmail, store, 1, clock=lambda: 1)
    with pytest.raises(RuntimeError, match="different recent-mail scope"):
        configure_days_scope(gmail, store, 45)


def test_new_policy_rejects_old_model_receipts(store):
    store.set("classifier_policy", "typesafe-jev-job-mail-v1")
    gmail = FakeGmail([message()])
    with pytest.raises(RuntimeError, match="different classifier policy"):
        synchronize(gmail, store, label_only=True)
    assert not gmail.fetched


def test_all_replies_in_same_thread_are_classified_independently(store):
    gmail = FakeGmail([message("first"), message("reply"), message("personal", subject="Dinner")])
    result = synchronize(gmail, store, label_only=True)
    assert result.matched == 2
    assert [mid for mid, _ in gmail.modified] == ["first", "reply"]
