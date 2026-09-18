# Seed data

Everything in here is fictional — invented companies and people with plausible-looking `.co.uk` domains. Nothing maps to a real business.

| File | Purpose |
|---|---|
| `seed_emails.py` | **Source of truth** for the 30 demo emails, their attachments and the `expected` outcome of each document |
| `make_attachments.py` | Regenerates `attachments/`, `attachments/manifest.json` and `emails.json` deterministically |
| `emails.json` | Generated. What the reset job sends and what the tests assert against |
| `attachments/` | Generated PDFs / CSV / ZIP / one deliberately corrupt PDF. `manifest.json` holds each file's sha256 |
| `suppliers.csv`, `customers.csv` | Party master data. `identifier_patterns` are `|`-separated tokens (email domains, reference prefixes) |
| `rules.csv` | Initial contents of the Google Sheet `rules` tab (also loaded into the `rules` table as fallback) |

Regenerate: `python seed/make_attachments.py`. Load master data: `python scripts/load_seed.py`.

## Matching semantics (implemented in `agent/matching.py`, Phase 2)

- Patterns are matched case-insensitively against the sender address, subject, body and attachment text.
- A pattern ending in `-` is a reference prefix and must be followed by a digit (`ACME-2031`, not `acme-`).
- One party matched → confident. Zero or two+ → held. A party name appearing in the text without any
  identifier → held as "matched by name only" (names are never trusted on their own).

## Scenario map

| # | Scenario | Expected |
|---|---|---|
| 1–8 | clean invoices, known supplier, within PO tolerance | auto_approved |
| 9–10 | over £2,000 | held |
| 11 | two suppliers share the TradePay identifier | held (ambiguous) |
| 12 | unknown supplier | held |
| 13 | exact duplicate of #3 | ignore (duplicate_of) |
| 14 | re-issued PDF, same invoice number as #4 | held |
| 15 | ZIP: invoice + catalogue | auto_approved + ignore |
| 16 | corrupt PDF | held |
| 17 | CSV invoice | auto_approved |
| 18–23 | clean disputes | auto_approved (ticket + draft) |
| 24 | refund £850 | held |
| 25 | name-only match | held |
| 26 | invoice + dispute letter in one email | two documents |
| 27 | legal threat | held |
| 28 | newsletter | ignore |
| 29 | prompt injection | held |
| 30 | remittance advice | auto_approved (Slack only) |
| 31 | known supplier, invoice carries changed bank details | held (verify by phone) |
