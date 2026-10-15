"""Small Gmail boundary. Its only mail mutation adds JEV labels to a single message."""

import base64
import hashlib
import os
from dataclasses import dataclass
from html.parser import HTMLParser

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
ALLOWED_LABELS = frozenset(
    {
        "JEV",
        *(
            f"JEV/{name}"
            for name in (
                "Applied",
                "Assessment",
                "Interview",
                "Offer",
                "Rejected",
                "Update",
                "JobAlert",
                "Opportunity",
            )
        ),
    }
)


class StaleHistory(Exception):
    """Gmail no longer retains the saved cursor."""


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    subject: str
    body: str
    received_ms: int


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(payload):
    """Read inline text only; never fetch attachments. Prefer plain over HTML alternatives."""
    if payload.get("filename") or payload.get("mimeType") == "message/rfc822":
        return ""
    parts = payload.get("parts", [])
    if parts:
        if payload.get("mimeType") == "multipart/alternative":
            ordered = sorted(parts, key=lambda p: p.get("mimeType") != "text/plain")
            return next((text for part in ordered if (text := body_text(part))), "")
        return "\n".join(body_text(part) for part in parts)[:32000]
    if payload.get("mimeType") not in {"text/plain", "text/html"}:
        return ""
    data = payload.get("body", {}).get("data", "")
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    text = raw.decode("utf-8", errors="replace")[:32000]
    if payload.get("mimeType") == "text/html":
        parser = _Text()
        parser.feed(text)
        return " ".join(parser.parts)
    return text


def save_token(path, credentials):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(credentials.to_json())
    os.replace(temporary, path)


def connect(settings, *, interactive=False):
    settings.prepare()
    credentials = None
    if settings.token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(settings.token_file), SCOPES)
    if credentials and not credentials.valid and credentials.refresh_token:
        credentials.refresh(Request())
        save_token(settings.token_file, credentials)
    if not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError("Gmail authorization needed; run jev-inbox auth first.")
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.client_file), SCOPES)
        credentials = flow.run_local_server(port=0, timeout_seconds=180)
        save_token(settings.token_file, credentials)
    if not credentials.has_scopes(SCOPES):
        raise RuntimeError("Gmail token lacks the required scope; authorize again.")
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
    return Gmail(build("gmail", "v1", http=http, cache_discovery=False))


class Gmail:
    def __init__(self, service):
        self.users = service.users()
        self.label_ids = None

    @staticmethod
    def _execute(request):
        return request.execute(num_retries=3)

    def profile(self):
        profile = self._execute(self.users.getProfile(userId="me"))
        identity = hashlib.sha256(profile["emailAddress"].casefold().encode()).hexdigest()
        return identity, profile["historyId"]

    def inbox_ids(self, *, query=None):
        page = None
        while True:
            result = self._execute(
                self.users.messages().list(
                    userId="me",
                    labelIds=["INBOX"],
                    includeSpamTrash=False,
                    q=query,
                    maxResults=500,
                    pageToken=page,
                )
            )
            yield from (item["id"] for item in result.get("messages", []))
            page = result.get("nextPageToken")
            if not page:
                break

    def changes(self, cursor):
        ids = set()
        page = None
        while True:
            try:
                result = self._execute(
                    self.users.history().list(
                        userId="me",
                        startHistoryId=cursor,
                        pageToken=page,
                        maxResults=500,
                        historyTypes=["messageAdded", "labelAdded"],
                    )
                )
            except HttpError as error:
                if error.resp.status == 404:
                    raise StaleHistory from None
                raise
            for record in result.get("history", []):
                ids.update(item["message"]["id"] for item in record.get("messagesAdded", []))
                ids.update(
                    item["message"]["id"]
                    for item in record.get("labelsAdded", [])
                    if "INBOX" in item.get("labelIds", [])
                )
            page = result.get("nextPageToken")
            if not page:
                return sorted(ids), result["historyId"]

    def _get(self, message_id, fmt):
        try:
            return self._execute(
                self.users.messages().get(
                    userId="me",
                    id=message_id,
                    format=fmt,
                )
            )
        except HttpError as error:
            if error.resp.status == 404:
                return None
            raise

    @staticmethod
    def _inbox(raw):
        labels = set((raw or {}).get("labelIds", []))
        return "INBOX" in labels and not labels.intersection({"TRASH", "SPAM", "DRAFT"})

    def inbox_message(self, message_id):
        # History may include non-INBOX IDs. Do not retrieve their bodies.
        if not self._inbox(self._get(message_id, "minimal")):
            return None
        raw = self._get(message_id, "full")
        if not self._inbox(raw):
            return None
        payload = raw.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        return Message(
            raw["id"],
            raw["threadId"],
            headers.get("subject", "")[:500],
            body_text(payload),
            int(raw["internalDate"]),
        )

    def _label_id(self, name):
        if self.label_ids is None:
            result = self._execute(self.users.labels().list(userId="me"))
            self.label_ids = {label["name"]: label["id"] for label in result.get("labels", [])}
        if name not in self.label_ids:
            try:
                label = self._execute(
                    self.users.labels().create(
                        userId="me",
                        body={
                            "name": name,
                            "labelListVisibility": "labelShow",
                            "messageListVisibility": "show",
                        },
                    )
                )
                self.label_ids[name] = label["id"]
            except HttpError as error:
                if error.resp.status != 409:
                    raise
                result = self._execute(self.users.labels().list(userId="me"))
                self.label_ids = {label["name"]: label["id"] for label in result["labels"]}
        return self.label_ids[name]

    def add_labels(self, message_id, names):
        names = tuple(names)
        if not names or not set(names).issubset(ALLOWED_LABELS):
            raise ValueError("Only predefined JEV labels may be added.")
        if not self._inbox(self._get(message_id, "minimal")):
            return False
        desired = {self._label_id(name) for name in names}
        # Recheck after label creation, immediately before modification.
        latest = self._get(message_id, "minimal")
        if not self._inbox(latest):
            return False
        missing = sorted(desired - set(latest.get("labelIds", [])))
        if missing:
            try:
                self._execute(
                    self.users.messages().modify(
                        userId="me",
                        id=message_id,
                        body={"addLabelIds": missing},
                    )
                )
            except HttpError as error:
                if error.resp.status == 404:
                    return False
                raise
        return True
