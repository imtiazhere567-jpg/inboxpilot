# Ops Agent — invoices & disputes

A live, clickable portfolio demo of an AI agent working inside business operations: it reads a shared inbox of
supplier invoices and customer disputes, classifies and extracts with Claude, verifies its own extraction with a
second model, applies rules a non-developer can edit in a Google Sheet, executes safe items into QuickBooks /
HubSpot / Slack, holds risky ones for a human with a reason, and records every decision in a ledger.

Full specification: [PLAN.md](PLAN.md) (read the **Amendments** section at the end first).

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · Postgres · APScheduler · Anthropic SDK (Claude Sonnet 5 + Haiku 4.5) ·
gspread (rules sheet) · IMAP/SMTP (Gmail demo account) · hubspot-api-client · python-quickbooks · slack_sdk ·
one static HTML page. Deployed on Railway (web service + Postgres). No n8n, no Docker.

## Layout

```
agent/
  main.py              FastAPI routes (+ SSE)               scripts/init_db.py   create DB + apply schema
  config.py            Settings (env only)                  sql/schema.sql       the schema (authoritative)
  db.py                engine/session/apply_schema          seed/                30 seed emails + attachments
  models.py            ORM                                  tests/               pytest (contract = 30 expected outcomes)
  schemas.py           Pydantic shapes                      docs/inside.html     under-the-hood page
  llm.py               Claude calls (tool-use schemas)      docs/how-id-build-yours.md
  rules.py             Google Sheet rules (60 s cache)
  matching.py          deterministic party matching (before any LLM)
  pipeline/            intake · process · actions · reset  (the former n8n flows)
  integrations/        gmail · sheets · hubspot · qbo · slack
  scheduler.py         APScheduler jobs
  static/index.html    review queue page
```

## Run locally

```powershell
cd "Ops Agent"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env           # then fill in DATABASE_URL (local Postgres password) at minimum
python scripts\init_db.py        # creates database ops_agent + applies schema
uvicorn agent.main:app --reload  # http://localhost:8000  ->  /health
```

## Test

```powershell
pytest                            # uses APP_ENV=test, LLM_FAKE=true, never touches external systems
pytest --cov=agent                # coverage target 80 %
```

## Reset the demo

`POST /reset` with `Authorization: Bearer $RESET_TOKEN` (Phase 5), or the **Reset demo** button on the page.
The scheduler also resets on `RESET_INTERVAL_MINUTES` when the page has been idle for `RESET_IDLE_MINUTES`.

## Phases

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo, schema, settings, FastAPI `/health`, credential list | ✅ |
| 1 | Seed data: 30 emails + attachments, customers/suppliers/rules CSVs, `test_seed.py` | ✅ |
| 2 | Agent service: classify/extract/verify/draft, matching, rules; `test_pipeline.py` 32/32 | ✅ fake · ⏳ real model |
| 3 | Gmail intake + inject endpoint, QBO/HubSpot/Slack actions | |
| 4 | Review page (SSE, approve/reject/retry, ledger, CSV) | |
| 5 | Shadow mode + idle-aware reset + countdown | |
| 6 | Under-the-hood page, scorecard, Loom, one-pager | |
| 7 | Hardening: login, rate limits, error states, 24 h unattended | |
