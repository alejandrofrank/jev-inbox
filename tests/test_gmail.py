import base64
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jev_inbox.gmail import Gmail, StaleHistory, body_text


def client():
    service = MagicMock()
    return Gmail(service, min_interval=0), service.users.return_value


def test_request_pacing_prevents_a_get_request_burst():
    current_time = [100.0]
    starts = []

    def sleep(seconds):
        current_time[0] += seconds

    request = MagicMock()
    request.execute.side_effect = lambda **kwargs: starts.append(current_time[0])
    gmail = Gmail(MagicMock(), clock=lambda: current_time[0], sleep=sleep)
    for _ in range(300):
        gmail._execute(request)
    # At most 172 requests / 3,440 get quota units can start in any 60-second window.
    assert max(sum(start <= other < start + 60 for other in starts) for start in starts) <= 172
    request.execute.assert_called_with(num_retries=5)


def error(status):
    return HttpError(httplib2.Response({"status": str(status)}), b"{}")


def raw(labels=None):
    return {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1000",
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD", "Personal"],
        "payload": {
            "headers": [{"name": "Subject", "value": "Application received"}],
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }


def test_lists_only_inbox_and_all_pages():
    gmail, users = client()
    users.messages().list().execute.side_effect = [
        {"messages": [{"id": "m1"}], "nextPageToken": "next"},
        {"messages": [{"id": "m2"}]},
    ]
    assert list(gmail.inbox_ids()) == ["m1", "m2"]
    calls = users.messages().list.call_args_list[-2:]
    assert all(c.kwargs["labelIds"] == ["INBOX"] for c in calls)
    assert all(c.kwargs["includeSpamTrash"] is False for c in calls)
    assert calls[1].kwargs["pageToken"] == "next"


def test_candidate_search_still_requires_inbox():
    gmail, users = client()
    users.messages().list().execute.return_value = {"messages": [{"id": "m1"}]}
    assert list(gmail.inbox_ids(query="{job application}")) == ["m1"]
    kwargs = users.messages().list.call_args.kwargs
    assert kwargs["q"] == "{job application}"
    assert kwargs["labelIds"] == ["INBOX"]
    assert kwargs["includeSpamTrash"] is False


@pytest.mark.parametrize("labels", [[], ["SENT"], ["INBOX", "TRASH"], ["INBOX", "SPAM"]])
def test_non_inbox_bodies_are_never_fetched(labels):
    gmail, users = client()
    users.messages().get().execute.return_value = raw(labels)
    assert gmail.inbox_message("m1") is None
    assert users.messages().get.call_args.kwargs["format"] == "minimal"


def test_rechecks_inbox_after_full_fetch():
    gmail, users = client()
    users.messages().get().execute.side_effect = [raw(), raw([])]
    assert gmail.inbox_message("m1") is None


def test_modify_only_adds_predefined_job_labels():
    gmail, users = client()
    users.messages().get().execute.return_value = raw()
    users.labels().list().execute.return_value = {"labels": [{"name": "grok", "id": "J1"}]}
    users.labels().create().execute.return_value = {"id": "J2"}
    assert gmail.add_labels("m1", ["grok", "grok/Applied"])
    assert users.messages().modify.call_args.kwargs == {
        "userId": "me",
        "id": "m1",
        "body": {"addLabelIds": ["J1", "J2"]},
    }
    users.threads.assert_not_called()
    for method in ("trash", "delete", "send", "batchModify"):
        getattr(users.messages(), method).assert_not_called()
    with pytest.raises(ValueError):
        gmail.add_labels("m1", ["TRASH"])


def test_archive_race_does_not_modify():
    gmail, users = client()
    gmail.label_ids = {"grok": "J1"}
    users.messages().get().execute.side_effect = [raw(), raw([])]
    assert not gmail.add_labels("m1", ["grok"])
    users.messages().modify.assert_not_called()


def test_replaying_existing_label_is_noop():
    gmail, users = client()
    gmail.label_ids = {"grok": "J1"}
    users.messages().get().execute.return_value = raw(["INBOX", "J1"])
    assert gmail.add_labels("m1", ["grok"])
    users.messages().modify.assert_not_called()


def test_history_pages_include_inbox_moves_and_deduplicate():
    gmail, users = client()
    users.history().list().execute.side_effect = [
        {
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
            "nextPageToken": "next",
            "historyId": "19",
        },
        {
            "history": [
                {
                    "labelsAdded": [
                        {"message": {"id": "m1"}, "labelIds": ["INBOX"]},
                        {"message": {"id": "m2"}, "labelIds": ["INBOX"]},
                        {"message": {"id": "m3"}, "labelIds": ["J1"]},
                    ]
                }
            ],
            "historyId": "20",
        },
    ]
    assert gmail.changes("10") == (["m1", "m2"], "20")
    assert users.history().list.call_args.kwargs["pageToken"] == "next"


def test_empty_history_and_expired_history_are_distinct():
    gmail, users = client()
    users.history().list().execute.return_value = {"historyId": "21"}
    assert gmail.changes("20") == ([], "21")
    users.history().list().execute.side_effect = error(404)
    with pytest.raises(StaleHistory):
        gmail.changes("20")
    users.history().list().execute.side_effect = error(403)
    with pytest.raises(HttpError):
        gmail.changes("20")


def test_deleted_message_is_skipped():
    gmail, users = client()
    users.messages().get().execute.side_effect = error(404)
    assert gmail.inbox_message("m1") is None


def test_scope_metadata_does_not_fetch_headers_or_bodies():
    gmail, users = client()
    users.messages().get().execute.return_value = {
        "id": "m1",
        "labelIds": ["INBOX"],
        "internalDate": "12345",
    }
    assert gmail.inbox_received_ms("m1") == 12345
    kwargs = users.messages().get.call_args.kwargs
    assert kwargs["format"] == "metadata" and kwargs["fields"] == "id,labelIds,internalDate"


def test_latest_selection_sorts_receipt_times_not_list_order(monkeypatch):
    gmail, _ = client()
    queries = []

    def ids(*, query):
        queries.append(query)
        return ["old", "future", "newest", "second"]

    monkeypatch.setattr(gmail, "inbox_ids", ids)
    dates = {"old": 1000, "newest": 9000, "second": 8000, "future": 12000}
    monkeypatch.setattr(gmail, "inbox_received_ms", dates.get)
    assert gmail.latest_inbox_ids(2, before_ms=10000) == ["newest", "second"]
    assert queries == ["after:0 before:11 -in:drafts"]


def test_latest_selection_expands_time_window_until_enough_messages(monkeypatch):
    gmail, _ = client()
    queries, metadata = [], []

    def ids(*, query):
        queries.append(query)
        return ["new"] if len(queries) == 1 else ["old", "new"]

    def date(mid):
        metadata.append(mid)
        return {"old": 4000000, "new": 8000000}[mid]

    monkeypatch.setattr(gmail, "inbox_ids", ids)
    monkeypatch.setattr(gmail, "inbox_received_ms", date)
    assert gmail.latest_inbox_ids(2, before_ms=10000000) == ["new", "old"]
    assert queries == ["after:6400 before:10001 -in:drafts", "after:2800 before:10001 -in:drafts"]
    assert len(metadata) == 2


def part(mime, text, **extra):
    return {
        "mimeType": mime,
        "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
        **extra,
    }


def test_mime_text_prefers_plain_and_ignores_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    part("text/html", "<p>HTML alternative</p>"),
                    part("text/plain", "Plain content"),
                ],
            },
            part("text/plain", "Attachment content", filename="resume.txt"),
        ],
    }
    assert body_text(payload).strip() == "Plain content"
    assert body_text(part("text/html", "<style>hidden</style><p>Hello</p>")) == "\n Hello"
