# JEV Inbox

Python MVP that labels job-related messages **currently in Gmail INBOX**, tracks
application conversations in local SQLite, and provides an authenticated,
read-only JSON API for Grok Bot. No AI provider or paid API key is needed.

## Just organize job mail into a folder

After the Gmail authorization described below, run:

```sh
jev-inbox sync --label-only --max-messages 200
jev-inbox watch --label-only --max-messages 200 --interval 300
```

This mode adds **only the JEV label** (Gmail's folder-like organization) to
job-related INBOX messages. Messages stay in INBOX. It needs no JEV API token,
Grok configuration or API server. The first scan searches for likely job mail
before classification; subsequent scans use Gmail history. A separate private
`labels.sqlite3` file retains only processed IDs, dispositions and sync state,
with no email text or application summaries. Existing tracking/API features
remain optional and are not started by these commands.

`--max-messages 200` bounds each run. A result with `complete: false` means more
messages remain; the next run resumes using receipts. Partial runs keep the
original history checkpoint until all candidates have been inspected. No new
history is skipped when processing the initial inbox across several runs.

`watch` runs while its process and computer are running. For unattended local
operation, schedule `jev-inbox sync --label-only --max-messages 200` every five minutes under your
own Windows account, with overlapping runs disabled. Use the virtual environment's
`pythonw.exe -m jev_inbox sync --label-only --max-messages 200` to avoid opening a console window.
The PC must be awake and the account signed in when using an interactive scheduled
task. Google may require renewed consent when a test-app refresh token expires.

## Behavior

- Adds only `JEV` and a predefined `JEV/*` label to individual matching messages.
  Never removes labels, archives, deletes, marks read, sends mail, or modifies a
  whole thread. Unrelated mail receives no Gmail mutation.
- First run enumerates INBOX only. Later runs use Gmail history IDs and inspect
  new messages and moves into INBOX. Non-INBOX bodies and attachments are not
  retrieved. Spam, Trash and drafts are excluded even if labeled INBOX.
- Rechecks INBOX membership before reading the body and immediately before
  applying labels. Gmail has no conditional check-and-modify operation: a user
  moving a message in the tiny gap between those requests can still leave it
  with an added JEV label. This application never changes its INBOX membership.
- Commits the cursor and local changes together. A failed run keeps the previous
  checkpoint. Gmail label additions are idempotent, so replay after a crash is
  safe. An expired history ID triggers a fresh INBOX scan with deduplication.
- Uses one database per account and serializes sync writers. WAL permits API
  reads during sync. Full-scan cursors are captured **before** enumeration, so
  arrivals during a scan remain eligible on the next run.

| Label | Meaning | Application record? |
| --- | --- | --- |
| `JEV/Applied` | Application acknowledgment | Yes |
| `JEV/Assessment` | Technical assessment invitation | Yes |
| `JEV/Interview` | Interview invitation or scheduling | Yes |
| `JEV/Offer` | Employment offer | Yes |
| `JEV/Rejected` | Application rejection | Yes |
| `JEV/Update` | Explicit application update without a new status | Yes |
| `JEV/Opportunity` | Recruiting outreach | No |
| `JEV/JobAlert` | Job alerts and recommendations | No |

The deterministic English/Spanish rules are deliberately limited. They can miss
unusual wording and can misclassify ambiguous messages; they are not a trained
classifier. Rules live in `src/jev_inbox/classifier.py`. Subject evidence takes
priority and common quoted replies are excluded. Labels describe each message;
older status labels are never removed.

One Gmail thread is one application. Separate threads for the same application
remain separate; different applications in one thread are merged. Current status
comes from the latest substantive event by Gmail's internal message timestamp,
with message ID as a deterministic tie-breaker. Generic updates do not erase a
known status. The MVP does not infer company names, roles or application dates
from free text. It stores the subject and first/last received timestamps instead.

## Install

Requires Python 3.11 or newer. From this repository:

```sh
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on macOS/Linux, then:

```sh
python -m pip install -e ".[dev]"
jev-inbox --help
```

## Gmail authorization and sync

1. Follow Google's [Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
   to enable the Gmail API, configure OAuth consent, and create a **Desktop app**
   OAuth client. For an external app in testing, add your account as a test user.
2. Put the downloaded OAuth client file outside this repository. By default the
   app reads `~/.local/share/jev-inbox/credentials.json` on every platform.
3. Run `jev-inbox auth` and grant the requested Gmail access in your browser.
   OAuth refresh tokens are saved locally in the private data directory.
4. Run the preview, then sync:

```sh
jev-inbox sync --dry-run
jev-inbox sync
jev-inbox watch --interval 300
```

`sync` runs once and prints only aggregate counts. `watch` repeats until Ctrl+C,
retrying transient failures on the next interval. The preview reads eligible
messages and reports counts, but creates no labels, receipts, application rows
or cursor updates. It may create an empty database/schema and refresh the OAuth
token. Already processed messages are skipped, including during previews.

The Google scope is `https://www.googleapis.com/auth/gmail.modify`, which Gmail
requires to add message labels and which grants broader permissions than this
application exercises. The implementation exposes no send/archive/delete operation.
See Google's [message modification reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify).

Configuration is read from the running process. Set variables in your shell or
deployment secret manager. This repository intentionally contains **no environment
files or templates**, and does not load `.env` files.

| Variable | Default / purpose |
| --- | --- |
| `JEV_DATA_DIR` | `~/.local/share/jev-inbox`; private database and OAuth token directory |
| `JEV_GOOGLE_CLIENT_FILE` | `credentials.json` inside the data directory; OAuth client file path |
| `JEV_API_TOKEN` | Required only for `serve`; random bearer token of at least 32 ASCII characters |

Keep the data directory outside your checkout, in a directory accessible only
to your operating-system account. The app requests owner-only permissions on
new directories/token files on POSIX. On Windows, restrict directory access
using your account's NTFS permissions. SQLite and OAuth files are not encrypted
by this application.

## Read-only API for Grok Bot

Initialize the database with a sync, then generate a token in your current shell.
In PowerShell:

```powershell
$env:JEV_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
jev-inbox serve
```

In another authorized process, configure the same token through its secret
store. The API listens at `http://127.0.0.1:8000` by default. Every GET endpoint,
including health and OpenAPI, requires `Authorization: Bearer <token>`.

| GET endpoint | Returns |
| --- | --- |
| `/health` | Database availability |
| `/v1/summary` | Total applications, counts by status, last successful sync time |
| `/v1/applications` | Paginated summaries; `status`, `limit` (1–100), `offset` |
| `/v1/applications/{id}` | One application by opaque Gmail thread ID |
| `/openapi.json` | API schema with bearer authentication and GET operations |

Each summary contains `id`, `subject`, `status`, `first_received_ms`,
`last_received_ms`, `last_message_id` and `message_count`. Times ending in `_ms`
are Unix milliseconds; `last_sync` is an ISO 8601 UTC timestamp. Pagination sorts
by last received time, then ID, descending. Pages can shift when a sync adds data.
Responses use `Cache-Control: no-store`. There are no mutation endpoints.

A Grok Bot tool can issue this authenticated request using its own secret store:

```python
import os
import requests

response = requests.get(
    "http://127.0.0.1:8000/v1/applications",
    params={"status": "interview", "limit": 25},
    headers={"Authorization": "Bearer " + os.environ["JEV_API_TOKEN"]},
    timeout=10,
)
response.raise_for_status()
applications = response.json()["items"]
```

This is an integration API, not a deployed Grok Bot or an xAI client. A remote
bot needs a reachable private endpoint; use an authenticated TLS reverse proxy
or private tunnel and configure its URL in the bot. `serve --host 0.0.0.0` is
available for that deployment, but do not send bearer tokens over public HTTP.
Email subjects remain untrusted content: bots must treat them as data, not
instructions. No email text is automatically sent to Grok or any model.

## Local data and recovery

SQLite holds only application subjects, message/thread IDs, classification
reasons, timestamps, statuses, processed IDs, a hashed account identifier and
sync state. Bodies, attachments and sender/recipient addresses are not persisted.
For unrelated mail, only an opaque message ID and an `ignored` receipt are saved.
The API opens SQLite with `mode=ro` and `query_only=ON`; it has no Gmail client.
Access logs are disabled and CLI failures omit third-party exception text.

Already processed messages are not reclassified on subsequent runs. Manual
removal of a JEV label is respected. If rules change and you want a complete
reclassification, stop the worker and API, back up and relocate the whole local
database (including any SQLite sidecars), then run a new sync. Existing Gmail
labels stay in place. Archived/deleted applications remain in local history;
the API represents observed applications, not a live list of INBOX contents.

If a run fails, fix connectivity/authorization and run it again. `RuntimeError`
usually means missing Gmail authorization or an account/database mismatch;
`RefreshError` means reauthorization may be required. To reauthorize, stop the
worker, remove only the local OAuth token and run `auth` again. A database lock
means another synchronizer is running. A 503 API response means the database is
missing/unreadable; run sync and check file permissions. A 401 means the bearer
token is absent or different. An invalid/short API token prevents server startup.

Gmail history retention is finite; automatic recovery follows Google's
[sync guide](https://developers.google.com/workspace/gmail/api/guides/sync) and
[history reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list).
Messages that arrive and leave INBOX between runs are intentionally not processed.

## Tests and publication checks

```sh
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python scripts/scan_staged.py
```

Tests use synthetic mail and temporary databases, with no live Gmail or Grok
requests. They cover INBOX guards, add-only modifications, pagination, expired
cursors, replay, transaction failures, account isolation, status chronology,
read-only SQL, API authentication, query limits and secret scanning.

The publication scanner reads **Git's staged blobs**, not working files. It
rejects credential patterns, private email addresses, environment files/templates,
mail dumps, logs, databases, binary files and unexpected paths. It reports only
file names and finding categories. Run it before every commit and push; CI scans
the committed tree with `python scripts/scan_staged.py --revision HEAD`.
A heuristic scanner supplements code review; it cannot recognize every secret.

Never stage credentials, OAuth tokens, API keys, personal mail, logs, local
databases or environment templates, even if a file appears in `.gitignore`.
