# How I'd build an ops agent for your business

**What you saw in the demo:** an agent that reads a shared inbox, decides what each item is and who it's from,
extracts the fields that matter, checks its own work with a second model, applies rules you can edit in a sheet,
executes the safe items into your systems and holds the risky ones for a person — with a reason, in a ledger.

## The three questions I ask first

1. **Which documents, from whom?** Invoices, disputes, orders, applications, claims… and how you recognise the sender today.
2. **What would a careful person check before acting?** Those checks become the rules sheet and the human gate.
3. **Which systems must change as a result?** Accounting, CRM, ticketing, chat — each gets one idempotent action.

## What you get

| Week | Deliverable | You see |
|---|---|---|
| 1 | Seed set of 20–40 anonymised examples with an expected outcome each; rules sheet drafted | The acceptance test — nothing ships until it passes |
| 2 | Pipeline running in **shadow mode** against your sandbox accounts | What it *would* have done, item by item, with cost |
| 3 | Human review page (or Slack buttons), ledger + CSV, alerts, execution on for the safest category | Live, gated, auditable |

## Guardrails that come as standard

- Deterministic identity matching before any model call; ambiguous → held
- A second, cheaper model that can veto the first (verifier)
- Prompt-injection guard: the agent never follows instructions found inside an email
- Idempotent everything: replaying a message or retrying an action never duplicates
- Rules in configuration (Google Sheet / table), not code
- Shadow mode before execution; sandbox credentials until sign-off
- A ledger tracing email → decision → action → cost, with human overrides recorded

## Stack

Python 3.11 · FastAPI · Postgres · Claude (Sonnet 5 + Haiku 4.5) · your systems' official SDKs · one small web page.
Deploys to Railway, Fly, Render or your own VM. You own the code.

## Commercials

Fixed price for a defined scope (typically 2–3 weeks), sandbox-only until you sign off, documentation and a
hand-over walkthrough included. Model cost at your volume is quoted up front from the demo's measured figures.

**Contact:** contact@bigcashsnapper.com
