# JEV Inbox

Python organizer that uses **TypeSafe's Jev semantic decision model** to classify
messages currently in Gmail INBOX and apply job-mail labels. Optional SQLite
application tracking and a read-only API for Grok Bot are also available.
Gmail OAuth authorizes mailbox access; a separate **TypeSafe API key** authorizes
model decisions. The keyword classifier from the initial version has been removed.

## Just organize job mail into a folder

After Gmail authorization and TypeSafe key setup below, run:

```sh
jev-inbox scope --label-only --latest 100
jev-inbox sync --label-only --require-scope --max-messages 100
jev-inbox watch --label-only --require-scope --max-messages 100 --interval 300
```

`scope --latest 100` permanently selects the newest 100 eligible INBOX messages
by Gmail receipt time, then allows only arrivals from that starting point onward.
Selection reads IDs and timestamps, not bodies, and saves the history checkpoint
before enumeration. It does not call TypeSafe or change Gmail. The selected IDs
are saved so retries cannot drift into older messages. Re-running the same scope
command is a no-op; changing the number requires a new database and deliberate setup.
`--require-scope` stops safely if the saved scope is missing. Old messages moved
back into INBOX are excluded unless they were among the original selected 100.
Expired Gmail history recovers using the same arrival boundary and selected IDs,
never by sending the older mailbox to TypeSafe.

This mode adds **only the JEV label** (Gmail's folder-like organization) to
job-related INBOX messages. Messages stay in INBOX. It needs a TypeSafe key but
no Grok configuration or API server. The first scan passes each selected INBOX
message to Jev, with no keyword prefilter; subsequent scans use Gmail history.
A separate private `jev-labels.sqlite3` file retains IDs, decisions, confidence,
probabilities, model version and sync state, with no email text or application
summaries. Existing tracking/API features
remain optional and are not started by these commands.

`--max-messages` bounds each run; it is not a limit on the total initial scope.
A result with `complete: false` means more
messages remain; the next run resumes using receipts. Partial runs keep the
original history checkpoint until the current inbox has been inspected. No new
history is skipped when processing the initial inbox across several runs.
Requests are paced and use bounded exponential retries to respect Gmail's
[per-user rate limits](https://developers.google.com/workspace/gmail/api/reference/quota).

`watch` runs while its process and computer are running. For unattended local
operation, schedule `jev-inbox sync --label-only --require-scope --max-messages 100` every five minutes under your
own Windows account, with overlapping runs disabled. Use the virtual environment's
`pythonw.exe -m jev_inbox sync --label-only --require-scope --max-messages 100` to avoid opening a console window.
The PC must be awake and the account signed in when using an interactive scheduled
task. Google may require renewed consent when a test-app refresh token expires.

## Behavior

- Adds only `JEV` and a predefined `JEV/*` label to individual matching messages.
  Never removes labels, archives, deletes, marks read, sends mail, or modifies a
  whole thread. Unrelated mail receives no Gmail mutation.
- First run enumerates INBOX only. Later runs use Gmail history IDs and inspect
  new messages and moves into INBOX. Non-INBOX bodies and attachments are not
  retrieved. Spam, Trash and drafts are excluded even if labeled INBOX.
- Without a configured scope and without `--require-scope`, an explicitly run
  sync retains the original full-INBOX behavior. Use the scoped commands above
  when only recent messages and future arrivals are authorized.
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

Classification calls only the official [TypeSafe API](https://docs.typesafe.ai/api)
at `https://api.typesafe.ai/v1/systemone`, pinned to `jev-1.13.0`. One Choice question
asks about the message's meaning and selects a job category or `unrelated`.
Code applies labels only when the selected category's probability is at least
0.90 and confidence at least 0.80. These are conservative starting thresholds,
not a measured accuracy guarantee. Uncertain results leave Gmail unchanged and
are recorded separately in `model_decisions` for review. Model errors stop the
run without advancing its checkpoint; **there is no keyword fallback**.
Model response validation, redirects, and retries are handled in
`src/jev_inbox/classifier.py`. Labels describe each message; older status labels
are never removed automatically.

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
4. Get a TypeSafe API key from the official [TypeSafe console](https://console.typesafe.ai).
   Supply it as `TYPESAFE_API_KEY` through your secret manager, or save only the key
   in `~/.local/share/jev-inbox/typesafe.key` inside the protected private directory.
   This is separate from Google's OAuth files and from the optional API's
   `JEV_API_TOKEN`. Never save any of these files in the repository.
5. Run a bounded preview, then sync:

```sh
jev-inbox scope --label-only --latest 100
jev-inbox sync --label-only --require-scope --dry-run --max-messages 25
jev-inbox sync --label-only --require-scope --max-messages 100
jev-inbox watch --label-only --require-scope --max-messages 100 --interval 300
```

`sync` runs once and prints only aggregate counts. `watch` repeats until Ctrl+C,
retrying transient failures on the next interval. The preview reads eligible
messages and reports counts, but creates no labels, receipts, application rows
or cursor updates. It may create an empty database/schema and refresh the OAuth
token. It **does call TypeSafe and uses model credits**, just like a real sync.
Already processed messages are skipped, including during previews.

Each evaluation sends only the subject (up to 500 characters) and inline body
excerpt (up to 16,000 characters) to TypeSafe. Those excerpts may contain personal
information. Attachments, Gmail IDs, OAuth tokens, and separate address headers
are not sent to the model. No mail text or API response bodies are logged.
See TypeSafe's [data handling documentation](https://docs.typesafe.ai/models).

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
| `TYPESAFE_API_KEY` | TypeSafe model key; otherwise read private `typesafe.key` in the data directory |
| `JEV_API_TOKEN` | Required only for `serve`; random bearer token of at least 32 ASCII characters |

Keep the data directory outside your checkout, in a directory accessible only
to your operating-system account. The app requests owner-only permissions on
new directories/token files on POSIX. On Windows, restrict directory access
using your account's NTFS permissions. SQLite and OAuth files are not encrypted
by this application.

## Read-only API for Grok Bot

This optional mode requires `jev-inbox sync --max-messages 200` without
`--label-only` to populate `jev-applications.sqlite3`. It is unnecessary for folder
organization. Then generate a local API bearer token in your current shell.
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
instructions. This API does not send email text to Grok. The organizer sends
message excerpts to TypeSafe for semantic classification as described above.

## Local data and recovery

SQLite holds only application subjects, message/thread IDs, classification
reasons, timestamps, statuses, processed IDs, a hashed account identifier and
sync state. Model evidence includes version, category, probability, confidence
and outcome. Bodies, attachments and sender/recipient addresses are not persisted.
For unrelated mail, only an opaque message ID and an `ignored` receipt are saved.
The API opens SQLite with `mode=ro` and `query_only=ON`; it has no Gmail client.
Access logs are disabled and CLI failures omit third-party exception text.

Already processed messages are not reclassified on subsequent runs. Manual
removal of a JEV label is respected. If the classification policy changes and you want a complete
reclassification, stop the worker and API, back up and relocate the whole local
database (including any SQLite sidecars), then run a new sync. Existing Gmail
labels stay in place. Archived/deleted applications remain in local history;
the API represents observed applications, not a live list of INBOX contents.

Upgrading from the initial keyword implementation uses new `jev-labels.sqlite3`
and `jev-applications.sqlite3` files. Old `labels.sqlite3` and `applications.sqlite3`
are preserved and their receipts never count as Jev evaluations. Existing Gmail
labels stay in place; they are not evidence of model decisions. Review previous
rule-produced labels separately before treating the folder as model-verified.

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

Tests use synthetic mail, mocked TypeSafe responses and temporary databases, with no live Gmail,
TypeSafe or Grok
requests. They cover INBOX guards, add-only modifications, pagination, expired
cursors, replay, transaction failures, account isolation, status chronology,
read-only SQL, API authentication, query limits and secret scanning. Model tests
cover the official endpoint, semantic input without keyword filtering, confidence
gating, malformed responses, missing keys, no rules fallback, bounded retries,
redirect rejection, and separation of legacy rule receipts from model evidence.

The publication scanner reads **Git's staged blobs**, not working files. It
rejects credential patterns, private email addresses, environment files/templates,
mail dumps, logs, databases, binary files and unexpected paths. It reports only
file names and finding categories. Run it before every commit and push; CI scans
the committed tree with `python scripts/scan_staged.py --revision HEAD`.
A heuristic scanner supplements code review; it cannot recognize every secret.

Never stage credentials, OAuth tokens, API keys, personal mail, logs, local
databases or environment templates, even if a file appears in `.gitignore`.
