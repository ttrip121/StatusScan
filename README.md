# StatusScan

Status Scan is a PM's daily assistant. It scans every task across your PM tools, flags
anything late or due today, cross-references Slack/Teams/Outlook to figure out whether each
flagged task is blocked on you or blocked on someone else, and emails a prioritized digest on
a schedule — so you know exactly who to follow up with, without opening every board.

## How it works

```
main.py
  1. Pull open tasks from every active TaskSource (Asana, Monday, ...)
  2. Flag tasks whose due date is today or in the past
  3. Build a keyword set per flagged task (name, project, tags)
  4. Search every active ContextSource (Slack, Teams, Outlook) for matching messages
     within the configured lookback window
  5. If matches were found, ask Claude to classify: blocked on the PM, blocked on someone
     else, or unclear - plus who it's waiting on and a reason (detail depends on the
     configured detail level - see Settings UI below)
  6. At the "Most" detail level, run one additional synthesis_pass() across every flagged
     task to surface cross-task patterns and suggested next steps
  7. Rank flagged tasks within each category by lateness and client-facing status
  8. Build the HTML digest (Insights section + three ranked sections) and email it
```

### Architecture: two adapter layers

- **`task_sources/`** — one file per PM tool, each implementing the `TaskSource` interface
  (`base.py`): `get_open_tasks()`, `get_due_date()`, `get_assignee()`, `get_tags()`, plus a
  few more fields to fully populate the common `Task` shape. Ships `asana.py` and
  `monday.py`. Adding ClickUp, Jira, or Wrike is a new file in this directory plus one line
  registering it in `main.py`'s `TASK_SOURCE_REGISTRY` — nothing else changes.

- **`context_sources/`** — one file per communication platform, each implementing the
  `ContextSource` interface (`base.py`): `search(keywords, lookback_days)`. Ships
  `slack.py`, `teams.py`, and `outlook.py`. `outlook.py` searches the PM's own mailbox and
  every configured shared mailbox.

Every adapter normalizes into a common shape (`models.py`):

- `Task`: `{id, name, project, due_date, assignee, tags, client_facing, url, source_platform}`
- `Message`: `{platform, channel_or_thread, author, timestamp, text, permalink}`

## Project layout

```
statusscan/
  models.py                 Task / Message / Classification / Synthesis dataclasses
  config.py                 Loads config.yaml, interpolates ${ENV_VAR} secrets
  settings.py                settings.json load/save/bootstrap + detail-level constants
  run_history.py             Lightweight "last N runs" log for the settings UI
  task_sources/
    base.py                 TaskSource interface
    asana.py
    monday.py
  context_sources/
    base.py                 ContextSource interface
    graph_auth.py            Shared Microsoft Graph app-only auth (Teams + Outlook)
    slack.py
    teams.py
    outlook.py
  classifier.py              Anthropic API calls: per-task classification + synthesis_pass()
  digest.py                  Ranking + HTML digest builder (Insights section + 3 sections)
  main.py                    Orchestrates one full run
  scheduler.py                15-minute poller that runs the pipeline on settings.json's schedule
settings_app.py               Streamlit settings UI - run with `streamlit run settings_app.py`
settings.json                 Generated on first use - sweep times, detail level, lookback,
                               recipients, active project/board scope (gitignored)
run_history.json               Generated on first use - recent-run log for the settings UI (gitignored)
config/
  config.example.yaml        Config template - copy to config.yaml
.env.example                 Secrets template - copy to .env
requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

Fill in `.env` with your credentials, and edit `config/config.yaml` for credentials, which
sources are active, and their starting scope. See the comments in `config.example.yaml` for
every field.

Run one pass by hand:

```bash
python -m statusscan.main
```

The first run (or the first time you open the settings UI) creates `settings.json`,
bootstrapped from whatever was already in `config.yaml`. After that, use the settings UI
(below) to change sweep frequency, detail level, lookback window, recipients, and active
project/board scope — `config.yaml` is no longer consulted for those fields.

## Settings UI

```bash
streamlit run settings_app.py
```

A local page for the PM to adjust the knobs they actually change day-to-day, without editing
YAML:

- **Sweep frequency** — add/remove times of day StatusScan runs. The poller (see
  *Scheduling* below) checks `settings.json` every 15 minutes and fires any slot whose time
  has been reached, so a change here takes effect on the next check — no restart.
- **Digest detail level** — `Some` / `More` / `Most`, each with a one-line description (see
  *Detail levels* below).
- **Lookback window, email recipients, active projects/boards** — the config fields a PM
  tunes most often, surfaced here for convenience. Credentials and which sources are *active*
  still live only in `config.yaml`/`.env`.
- **Recent runs** — the last 5 runs (timestamp, tasks flagged, recipients sent to), once
  `run_history.json` has at least one entry. Hidden until then.

Settings persist to `settings.json`, read by both this app and the pipeline
(`main.py`/`scheduler.py`) — there's exactly one file to keep in sync, and it's gitignored
since it's local runtime state, not shared config.

### Detail levels

| Level | Behavior |
|-------|----------|
| **Some** | Structured fields only (`blocked_on`, `waiting_on`, `reason`, `confidence`) — `reason` is one flat, factual sentence. |
| **More** | Same fields, but `reason` becomes 2-3 sentences of narrative framing. |
| **Most** | Same as More, plus a `synthesis_pass()` step: after every flagged task is classified, one additional Anthropic call looks across all of them for cross-task patterns (e.g. several tasks blocked on the same person) and suggests 2-4 concrete next steps. Rendered as an "Insights & Suggested Next Steps" section at the top of the digest, above the three normal sections. |

## Required API scopes / permissions per platform

### Asana

- Create a **Personal Access Token**: Asana → your profile settings → *Apps* → *Manage
  Developer Apps* → *Personal access tokens* → *New access token*.
- The token's owner needs read access to every project listed under
  `task_sources.asana.project_gids`.
- No special scope selection is needed for a PAT — access is whatever that user account can
  see. If you use OAuth instead of a PAT, request the `default` scope.

### Monday.com

- Generate a personal **API token**: Monday avatar (bottom-left) → *Administration* → *API*,
  or per-user under *Profile* → *Developers* → *My Access Tokens*.
- The token needs `boards:read` on every board listed under `task_sources.monday.board_ids`.
- Monday has no fixed "due date"/"tags" schema — these are custom columns. Find each board's
  column IDs via *"..."* menu → *Manage columns*, or by querying
  `boards(ids:[...]) { columns { id title type } }` with the GraphQL API, and set
  `due_date_column_id` / `tags_column_id` / `status_column_id` / `people_column_id`
  accordingly.

### Slack

- Slack's `search.messages` endpoint only works with a **user token** (`xoxp-...`) — bot
  tokens cannot search. Create a Slack app, install it under the PM's own account, and grant
  the **user token scope** `search:read`.
- To also match on channel names (`match_channel_names: true`), additionally grant
  `channels:read` (public channels) and `groups:read` (private channels).
- Search results are scoped to whatever the authorizing user can see — this is intentional,
  since the digest is meant to reflect the PM's own visibility.

### Microsoft Teams + Outlook (Microsoft Graph)

Both adapters share one Azure AD **app registration** using the OAuth2 **client-credentials**
(app-only) flow — set `microsoft_graph.tenant_id` / `client_id` / `client_secret` once and
reference the same block from both `teams` and `outlook` in config.

1. In Azure Portal → *App registrations* → *New registration*. Note the **Application
   (client) ID** and **Directory (tenant) ID**.
2. *Certificates & secrets* → new **client secret**. Use its value for `client_secret`.
3. *API permissions* → *Add a permission* → *Microsoft Graph* → **Application permissions**
   (not delegated), add:
   - `ChannelMessage.Read.All` (Teams channel messages)
   - `Chat.Read.All` (Teams 1:1/group chats)
   - `Team.ReadBasic.All` (resolve team/channel display names)
   - `Mail.Read` (Outlook — PM mailbox + shared mailboxes)
4. Click **Grant admin consent** for the tenant — application permissions do nothing without
   this.
5. **Scope down `Mail.Read`** (strongly recommended): by default, application-level
   `Mail.Read` can read every mailbox in the tenant. Restrict the app to only the mailboxes
   it needs with an Exchange Online **application access policy**:

   ```powershell
   New-DistributionGroup -Name "StatusScanMailboxes" -Members "pm@example.com","projects@example.com"
   New-ApplicationAccessPolicy -AppId "<client-id>" `
     -PolicyScopeGroupId "StatusScanMailboxes" -AccessRight RestrictAccess `
     -Description "Restrict StatusScan app to configured mailboxes"
   ```

6. List every mailbox you want searched in config: `outlook.pm_mailbox` (the PM's own
   mailbox) and `outlook.shared_mailboxes` (any shared mailboxes to include).

### Anthropic API

- Create an API key at [console.anthropic.com](https://console.anthropic.com) and set
  `ANTHROPIC_API_KEY` in `.env`.
- `classifier.py` defaults to `claude-opus-5`; set `anthropic.model` in config to
  `claude-sonnet-5` or `claude-haiku-4-5` for lower cost/latency at the expense of nuance —
  this is a straightforward classification task, so a smaller model is often good enough at
  scale.

### Email / SMTP

- Any standard SMTP relay works (Gmail, SES, SendGrid SMTP, your company's mail server,
  etc.). Fill in `email.smtp.host/port/username/password/from_address` and
  `email.recipients`.

## Scheduling

The default schedule is 3x/day — 8:00 AM, 1:00 PM, 6:00 PM — fully customizable from the
settings UI (`sweep_times` in `settings.json`). Two ways to run it:

### Option A: the poller (recommended - frequency changes take effect live)

```bash
python -m statusscan.scheduler
```

A long-running loop that checks `settings.json` every 15 minutes and runs the digest
pipeline once the current time reaches any configured slot (each slot fires once per day).
Because it re-reads `settings.json` on every check, changing sweep times in the settings UI
takes effect within 15 minutes — no restart. Keep it alive with your process manager of
choice. Example `systemd` unit:

```ini
[Unit]
Description=StatusScan poller
After=network.target

[Service]
WorkingDirectory=/opt/statusscan
ExecStart=/opt/statusscan/.venv/bin/python -m statusscan.scheduler
Restart=on-failure
EnvironmentFile=/opt/statusscan/.env

[Install]
WantedBy=multi-user.target
```

### Option B: plain cron (no long-running process)

Run `python -m statusscan.main` directly at fixed times — cron owns the schedule instead of
the poller:

```cron
0 8,13,18 * * * cd /opt/statusscan && /opt/statusscan/.venv/bin/python -m statusscan.main >> /var/log/statusscan.log 2>&1
```

Trade-off: cron doesn't read `settings.json`'s `sweep_times`, so changing sweep frequency in
the settings UI has no effect here — you'd need to edit the crontab too. Everything else the
settings UI controls (detail level, lookback window, recipients, active scope) still applies,
since `main.py` reads `settings.json` on every run regardless of what triggered it.

## Adding a new PM tool or communication platform

- **New PM tool** (ClickUp, Jira, Wrike, ...): add `task_sources/<platform>.py`
  implementing `TaskSource` (see `task_sources/base.py`), register the class in
  `main.py`'s `TASK_SOURCE_REGISTRY`, and add its config block under `task_sources:`.
- **New communication platform**: add `context_sources/<platform>.py` implementing
  `ContextSource` (see `context_sources/base.py`), register it in `main.py`'s
  `CONTEXT_SOURCE_REGISTRY`, and add its config block under `context_sources:`.

No other file needs to change in either case.
