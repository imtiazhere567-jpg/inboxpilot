---
title: InboxPilot
emoji: 📬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Ops Agent — invoices & disputes

A live, clickable portfolio demo of an AI agent working inside business operations: it reads a shared inbox of
supplier invoices and customer disputes, classifies and extracts with Claude, verifies its own extraction with a
second model, applies rules a non-developer can edit in a Google Sheet, executes safe items into QuickBooks /
HubSpot / Slack, holds risky ones for a human with a reason, and records every decision in a ledger.

Full specification: [PLAN.md](PLAN.md) (read the **Amendments** section at the end first).

## What a visitor sees

Open the page → 30 seeded emails stream in and get classified, matched and decided → 19 execute on their own,
10 are held with a one-line reason, 3 are ignored → click a held item, read *why*, press **Approve** → it posts
to QuickBooks / HubSpot / Slack (or is marked *simulated* when those aren't connected) → the ledger shows every
decision with its source snippet, verifier result, confidence and cost. Seed #29 is a prompt-injection attempt;
it is held and labelled **injection blocked**. The demo resets itself when idle.

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · Postgres · APScheduler · Anthropic SDK (Claude Sonnet 5 + Haiku 4.5,
structured outputs) · gspread (rules sheet) · IMAP/SMTP (Gmail demo account) · hubspot-api-client ·
python-quickbooks · slack_sdk · one static HTML page with SSE. Deployed on Railway (web service + Postgres).
No n8n, no Docker.

## Layout

```
agent/
  main.py              FastAPI routes, auth gate, rate limit, SSE      scripts/init_db.py   create DB + apply schema
  service.py           read models: queue, review, ledger, status      scripts/load_seed.py upsert master data
  config.py            Settings (env only)                             sql/schema.sql       the schema (authoritative)
  db.py                engine / session / schema / reset               seed/                30 seed emails + attachments
  models.py, schemas.py                                                tests/               51 tests, pipeline contract
  llm.py               Claude calls (+ deterministic fake)             docs/inside.html     under-the-hood page
  rules.py             rules sheet/table, injection guard, evaluate()
  matching.py          deterministic party matching (before any model)
  textextract.py       PDF / CSV / ZIP → text
  events.py            change signal for SSE
  pipeline/            intake · process · actions · reset · poll
  integrations/        gmail · sheets · hubspot · qbo · slack
  scheduler.py         APScheduler jobs (poll, idle-aware reset, auto-seed)
  static/              index.html · login.html · prompts.json · scorecard.json · how-id-build-yours.md
```

## Run locally (no accounts needed)

```powershell
cd "Ops Agent"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env             # set DATABASE_URL (local Postgres password); everything else can stay blank
python scripts\init_db.py          # creates database ops_agent + applies schema
python scripts\load_seed.py        # suppliers, customers, rules
uvicorn agent.main:app --reload    # http://localhost:8000 — seeds itself on first start
```

With no `ANTHROPIC_API_KEY` the service uses a deterministic **fake model** (keyword heuristics) so the whole
flow works offline; downstream systems that aren't configured are recorded as **simulated**. The page and the
under-the-hood page both say which parts are live.

## Test

```powershell
pytest                              # 51 tests; APP_ENV=test, LLM_FAKE=true, nothing external is called
pytest --cov=agent                  # coverage (integration clients only run with real credentials)
$env:RUN_REAL_LLM="1"; pytest tests/test_pipeline.py -s   # Phase 2 acceptance with Claude; writes static/scorecard.json
```

`tests/test_pipeline.py` is the contract: every seed email must land on its `expected` outcome (type, party,
status, reason, extracted amounts). It passes 32/32 with the fake model; run it with the real model once a key
is available.

## Accounts to create (all free / sandbox) and their variables

| Phase | Account | Where | Variables |
|---|---|---|---|
| 2 | Anthropic API key (~$5 credit) | platform.claude.com → API keys | `ANTHROPIC_API_KEY` |
| 2 | Google Sheet + service account | console.cloud.google.com → IAM → Service account → JSON key; share the sheet with its email | `GOOGLE_SERVICE_ACCOUNT_JSON`, `RULES_SHEET_ID` (sheet columns: key, value, note — paste `seed/rules.csv`) |
| 3 | Gmail demo account | new Gmail → 2-Step Verification → App passwords | `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` |
| 3 | HubSpot developer test account | developers.hubspot.com → test account → Private app (scopes: tickets r/w, companies r/w) | `HUBSPOT_ACCESS_TOKEN` |
| 3 | QuickBooks Online sandbox | developer.intuit.com → app → Keys (Development) → OAuth 2.0 Playground | `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REFRESH_TOKEN`, `QBO_REALM_ID` |
| 3 | Slack workspace + bot | api.slack.com/apps → Bot token scopes `chat:write`, `chat:write.public` → install | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL` |
| 6 | Railway | railway.app → New project → Postgres + this repo | `DATABASE_URL` (from the Postgres plugin), everything above, `APP_ENV=prod`, `APP_BASE_URL`, `DEMO_PASSWORD`, `RESET_TOKEN` |

Gmail note: seed emails are sent from the demo account to itself with `X-Ops-Agent-*` headers carrying the
intended sender; the poller reads `INBOX` every 30 s and moves processed mail to `ops-agent/processed`.

## Deploy (Railway)

1. New project → add **Postgres** → add a service from this repo. `railway.json` / `Procfile` start the app with
   `init_db.py` (creates schema) then uvicorn; health check is `/health`.
2. Set the variables above. Minimum for a public demo: `DATABASE_URL`, `APP_ENV=prod`, `APP_BASE_URL`,
   `DEMO_PASSWORD`, `RESET_TOKEN`, `ANTHROPIC_API_KEY`. Add the sandboxes as you create them — the page
   switches from *simulated* to live per system.
3. Custom domain `ops-agent.ranklocale.com` → the review queue; `/inside` → under the hood.
4. Set a spend cap on the Anthropic key ($30/month is plenty: ≈ 4 calls × 32 documents per reset).

## Reset & modes

- **Reset demo** button, or `POST /reset` with `Authorization: Bearer $RESET_TOKEN`.
- Scheduled reset every `RESET_INTERVAL_MINUTES` — deferred while someone used the page in the last
  `RESET_IDLE_MINUTES`; countdown shown in the header.
- **Shadow mode** toggle: evaluate everything, execute nothing (also writable in the rules sheet).
- **Outage** selector: make QuickBooks / HubSpot / Slack fail to show the Failed → Retry path.

## Phases

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo, schema, settings, FastAPI `/health`, credential list | ✅ |
| 1 | Seed data: 30 emails + attachments, master data, `test_seed.py` | ✅ |
| 2 | Agent service: classify/extract/verify/draft, matching, rules; `test_pipeline.py` | ✅ 32/32 fake · ⏳ real-model run needs `ANTHROPIC_API_KEY` |
| 3 | Gmail intake + `/inject`, QBO/HubSpot/Slack actions, idempotent retry, outage simulation | ✅ code + offline tests · ⏳ live check needs sandbox tokens |
| 4 | Review page: SSE stream, review panel, approve/reject/retry, ledger + CSV, summary tiles, timeline | ✅ |
| 5 | Shadow mode, idle-aware reset with countdown, auto-seed on first start | ✅ |
| 6 | Under-the-hood page (architecture, guardrails, live rules, scorecard, prompts), one-pager, Railway config | ✅ · ⏳ Loom video (record after deploy) |
| 7 | Demo login, rate limits, error states, restart policy | ✅ · ⏳ 24 h unattended soak after deploy |
