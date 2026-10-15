# JEV Inbox

Python organizer that uses **TypeSafe's Jev semantic decision model** to classify
messages currently in Gmail INBOX and place recruiter, application and ordinary work correspondence
in the **grok** Gmail label (folder). Interviews, recruiter replies, application updates and conversations about
existing jobs or freelance projects are included. Job alerts, digests and career
newsletters are excluded. Optional SQLite
application tracking and a read-only API for Grok Bot are also available.
Gmail OAuth authorizes mailbox access; a separate **TypeSafe API key** authorizes
model decisions. The keyword classifier from the initial version has been removed.

## Organize job mail into grok

After Gmail authorization and TypeSafe key setup below, run:

```sh
jev-inbox scope --label-only --latest 250
jev-inbox sync --label-only --require-scope --max-messages 100
jev-inbox watch --label-only --require-scope --max-messages 100 --interval 300
```

`scope --latest 250` permanently selects the newest 250 eligible INBOX messages
by Gmail receipt time, then allows only arrivals from that starting point onward.
Selection reads IDs and timestamps, not bodies, and saves the history checkpoint
before enumeration. It does not call TypeSafe or change Gmail. The selected IDs
are saved so retries cannot drift into older messages. Re-running the same scope
command is a no-op; changing the number requires a new database and deliberate setup.
For a deliberately authorized one-time date backfill, use a fresh v3 database:

```sh
jev-inbox scope --label-only --days 45
jev-inbox sync --label-only --require-scope --max-messages 100
```

This freezes the last 45 days of current INBOX IDs and the start time. It is not a
rolling scan of older mail. Each message's exact receipt timestamp is checked
before reading its body. Bounded runs resume the saved selection; new arrivals
are processed afterward using the checkpoint captured before selection.
Re-running the same scope command cannot expand the window. Changing between
`--days` and `--latest`, or changing their values, requires deliberate fresh setup.
Authorize sending the selected window's subjects and body excerpts to TypeSafe
before running it. Scope selection itself sends nothing to TypeSafe.

`--require-scope` stops safely if the saved scope is missing. Old messages moved
back into INBOX are excluded unless they were in the saved initial selection.
Expired Gmail history recovers using the same arrival boundary and selected IDs,
never by sending the older mailbox to TypeSafe.

This mode adds **only the grok label** (Gmail's folder-like organization) to
job-related INBOX messages. Messages stay in INBOX. It needs a TypeSafe key but
no Grok configuration or API server. The first scan passes each selected INBOX
message to Jev, with no keyword prefilter; subsequent scans use Gmail history.
A separate private `jev-labels-v3.sqlite3` file retains IDs, decisions, confidence,
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

- Adds only `grok` and a predefined `grok/*` label to individual matching messages.
  Normal syncing never removes labels, archives, deletes, marks read, sends mail,
  or modifies a whole thread. An explicit maintenance helper can remove only
  `grok` from a current INBOX message for a requested false-positive cleanup;
  it cannot remove INBOX, UNREAD or any other label. Unrelated mail receives no
  mutation during normal syncing.
- All Inbox categories, including Personal, are eligible. No existing `JEV` or
  `grok` label is required. Every message ID is classified independently, so
  later replies in an already seen thread are still processed.
- First run enumerates INBOX only. Later runs use Gmail history IDs and inspect
  new messages and moves into INBOX. Non-INBOX bodies and attachments are not
  retrieved. Spam, Trash and drafts are excluded even if labeled INBOX.
- Without a configured scope and without `--require-scope`, an explicitly run
  sync retains the original full-INBOX behavior. Use the scoped commands above
  when only recent messages and future arrivals are authorized.
- Rechecks INBOX membership before reading the body and immediately before
  applying labels. Gmail has no conditional check-and-modify operation: a user
  moving a message in the tiny gap between those requests can still leave it
  with an added grok label. This application never changes its INBOX membership.
- Commits the cursor and local changes together. A failed run keeps the previous
  checkpoint. Gmail label additions are idempotent, so replay after a crash is
  safe. An expired history ID triggers a fresh INBOX scan with deduplication.
- Uses one database per account and serializes sync writers. WAL permits API
  reads during sync. Full-scan cursors are captured **before** enumeration, so
  arrivals during a scan remain eligible on the next run.

| Label | Meaning | Application record? |
| --- | --- | --- |
| `grok/Applied` | Application acknowledgment | Yes |
| `grok/Assessment` | Technical assessment invitation | Yes |
| `grok/Interview` | Interview invitation or scheduling | Yes |
| `grok/Offer` | Employment offer | Yes |
| `grok/Rejected` | Application rejection | Yes |
| `grok/Update` | Explicit application update without a new status | Yes |
| `grok/Opportunity` | Recruiting outreach | No |
| `grok/Work` | Existing work, projects and client/colleague correspondence | No |

Classification calls only the official [TypeSafe API](https://docs.typesafe.ai/api)
at `https://api.typesafe.ai/v1/systemone`, pinned to `jev-1.13.0`. Three independent
[Noul questions](https://docs.typesafe.ai/primitives/noul) judge hiring correspondence,
ordinary professional work, and bulk alerts/digests separately. A separate Choice records the hiring stage. Folder membership
requires either hiring or work probability >= 0.80 and alert probability <= 0.20;
uncertainty about the exact stage cannot veto an otherwise clear match.
High-probability alerts (>= 0.80) or low probabilities for both hiring and work
(<= 0.20) are ignored; the middle band remains `uncertain` without a Gmail write.

Jev receives the sender domain and a limited sender-role hint, in addition to
subject and body. Domain matching includes actual subdomains, never lookalike
suffixes. Supported platform/recruiter hints include LinkedIn, Indeed, Glassdoor,
Jobgether, StatusNeo, Global M and common applicant tracking systems. Unknown
senders are still evaluated. Domains and subject words are evidence, never a
keyword allowlist that bypasses Jev. LinkedIn recruiter reply relays are identified;
its dedicated job-alert mailbox is excluded. Shared mailboxes, including
LinkedIn's jobs notifications, Glassdoor and Google Careers, can carry genuine
application updates and are classified by purpose instead of blanket blocking.

These thresholds are not a measured accuracy guarantee. Model errors stop the
run without advancing its checkpoint; **there is no keyword fallback**. Strict
response validation, redirects and retries are handled in
`src/jev_inbox/classifier.py`. Stage distributions allow bounded per-option
rounding error and are normalized; malformed probabilities still fail closed.
Normal sync never removes earlier status labels.

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
jev-inbox scope --label-only --latest 250
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

Each evaluation sends the subject (up to 500 characters), inline body excerpt
(up to 16,000 characters), sender domain and a generic sender-role hint to TypeSafe. Those excerpts may contain personal
information. Attachments, Gmail IDs, OAuth tokens, sender display names and
personal mailbox names from address headers are not sent to the model. No mail text or API response bodies are logged.
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
`--label-only` to populate `jev-applications-v3.sqlite3`. It is unnecessary for folder
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
| `/v1/applications` | Paginated summaries; `status`, `limit` (1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“100), `offset` |
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
and outcome, plus hiring/work/alert probabilities and the dedicated-sender
exclusion flag. Bodies, attachments and sender/recipient addresses are not persisted.
For unrelated mail, only an opaque message ID and an `ignored` receipt are saved.
The API opens SQLite with `mode=ro` and `query_only=ON`; it has no Gmail client.
Access logs are disabled and CLI failures omit third-party exception text.

Policy v3 uses separate `jev-labels-v3.sqlite3` and `jev-applications-v3.sqlite3`
files. Previous keyword, v1 and v2 model databases are preserved, and their decisions
are not reused as evidence under the new policy. Configure an explicitly approved
scope for v3 before restarting a scheduled task that requires one.

Already processed messages are not reclassified on subsequent runs. Manual
removal of a grok label is respected. If the classification policy changes and you want a complete
reclassification, stop the worker and API, back up and relocate the whole local
database (including any SQLite sidecars), then run a new sync. Existing Gmail
labels stay in place. Archived/deleted applications remain in local history;
the API represents observed applications, not a live list of INBOX contents.

Upgrading from the initial keyword implementation uses new `jev-labels-v3.sqlite3`
and `jev-applications-v3.sqlite3` files. Old `labels.sqlite3` and `applications.sqlite3`
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
requests. They cover INBOX guards, bounded grok cleanup, add-only normal sync, pagination, expired
cursors, replay, transaction failures, account isolation, status chronology,
read-only SQL, API authentication, query limits and secret scanning. Model tests
cover the official endpoint, semantic input without keyword filtering, confidence
gating independent of hiring-stage certainty, sender-domain boundaries, shared
senders, per-message replies, Personal category inclusion, fixed date backfills,
malformed responses, missing keys, no rules fallback, bounded retries,
redirect rejection, and separation of legacy rule receipts from model evidence.

The publication scanner reads **Git's staged blobs**, not working files. It
rejects credential patterns, private email addresses, environment files/templates,
mail dumps, logs, databases, binary files and unexpected paths. It reports only
file names and finding categories. Run it before every commit and push; CI scans
the committed tree with `python scripts/scan_staged.py --revision HEAD`.
A heuristic scanner supplements code review; it cannot recognize every secret.

Never stage credentials, OAuth tokens, API keys, personal mail, logs, local
databases or environment templates, even if a file appears in `.gitignore`.
