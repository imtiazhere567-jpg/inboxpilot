-- Ops Agent — invoices & disputes
-- Postgres schema. Applied by scripts/init_db.py (idempotent: safe to re-run).

CREATE TABLE IF NOT EXISTS customers (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    identifier_patterns TEXT[] NOT NULL DEFAULT '{}',   -- exact tokens: email domains, account refs, "ACME-" prefixes
    hubspot_company_id  TEXT,
    contact_email       TEXT
);

CREATE TABLE IF NOT EXISTS suppliers (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    identifier_patterns TEXT[] NOT NULL DEFAULT '{}',
    qbo_vendor_id       TEXT,
    iban_last4          TEXT,
    po_amount           NUMERIC(12,2)                      -- expected invoice amount; tolerance comes from rules
);

CREATE TABLE IF NOT EXISTS runs (
    id          SERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode        TEXT NOT NULL CHECK (mode IN ('live', 'shadow'))
);

CREATE TABLE IF NOT EXISTS emails (
    id                SERIAL PRIMARY KEY,
    gmail_message_id  TEXT NOT NULL UNIQUE,                -- idempotency guard for re-delivered mail
    seed_no           INT,                                 -- 1..30 for seeded demo mail, NULL otherwise
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    from_addr         TEXT NOT NULL,
    subject           TEXT,
    body_text         TEXT,
    ledger_status     TEXT NOT NULL DEFAULT 'received',    -- received | processed | ignored | failed
    run_id            INT REFERENCES runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id             SERIAL PRIMARY KEY,
    email_id       INT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    mime           TEXT,
    source_text    TEXT,                                   -- extracted text the LLM saw (for the review snippet)
    doc_type       TEXT,                                   -- supplier_invoice | customer_dispute | credit_note | remittance | ignore | unknown
    party_kind     TEXT,                                   -- supplier | customer | NULL
    party_id       INT,
    extracted      JSONB,
    confidence     NUMERIC(4,3),
    verify_result  JSONB,
    status         TEXT NOT NULL DEFAULT 'received',       -- received | auto_approved | held | approved | rejected | executed | failed | shadow | ignore
    reason         TEXT,
    draft_reply    TEXT,
    duplicate_of   INT REFERENCES documents(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (email_id, sha256)                              -- replaying the same email never creates a second row
);
CREATE INDEX IF NOT EXISTS documents_sha256_idx ON documents (sha256);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents (status);
CREATE INDEX IF NOT EXISTS documents_invoice_no_idx
    ON documents (party_id, (extracted->>'invoice_number'))
    WHERE extracted ? 'invoice_number';

CREATE TABLE IF NOT EXISTS decisions (
    id           SERIAL PRIMARY KEY,
    document_id  INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    step         TEXT NOT NULL,                            -- classify | match | extract | verify | rules | draft
    input        JSONB,
    output       JSONB,
    model        TEXT,
    tokens_in    INT NOT NULL DEFAULT 0,
    tokens_out   INT NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(10,6) NOT NULL DEFAULT 0,
    duration_ms  INT,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_document_idx ON decisions (document_id);

CREATE TABLE IF NOT EXISTS actions (
    id           SERIAL PRIMARY KEY,
    document_id  INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    system       TEXT NOT NULL,                            -- hubspot | qbo | slack
    external_id  TEXT,                                     -- id in the target system (for reset + idempotent retry)
    payload      JSONB,
    result       JSONB,
    status       TEXT NOT NULL DEFAULT 'ok',               -- ok | failed
    error        TEXT,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS actions_document_idx ON actions (document_id);

CREATE TABLE IF NOT EXISTS review_notes (
    id           SERIAL PRIMARY KEY,
    document_id  INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    action       TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'retry')),
    note         TEXT NOT NULL,
    by           TEXT NOT NULL DEFAULT 'demo',
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Local cache / fallback for the Google Sheet rules tab. rules.py writes here after each sheet read
-- and reads from here if the sheet is unreachable.
CREATE TABLE IF NOT EXISTS rules (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    note        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Single-row app state: last page interaction (for idle-aware reset), next scheduled reset, current run.
CREATE TABLE IF NOT EXISTS app_state (
    id                  INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_interaction_at TIMESTAMPTZ,
    next_reset_at       TIMESTAMPTZ,
    current_run_id      INT REFERENCES runs(id) ON DELETE SET NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO app_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
