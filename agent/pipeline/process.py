"""Process — classify → match → extract → verify → rules → draft (the former n8n 02-process flow).

Every step writes a `decisions` row (model steps carry tokens + cost; deterministic steps carry cost 0), so the
decisions table is both the audit trail and the per-document timeline.

Order of operations, and why:
  1. injection guard (regex) on the raw text          — cheapest and safest first
  2. classify (model)                                 — what is this?
  3. match (deterministic, no model)                  — who is it from? zero/2+ hits stop here
  4. extract (model)                                  — the fields we need
  5. verify (second, cheaper model)                   — is every extracted value literally in the source?
  6. duplicate-invoice-number check (DB)              — re-issued PDFs
  7. rules.evaluate                                   — status + reason
  8. draft reply (model, disputes only)               — held with the ticket, never auto-sent
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.llm import LLM, LLMCall, get_llm
from agent.matching import Party, match_any, match_party
from agent.models import Customer, Decision, Document, Email, Supplier
from agent.pipeline.intake import InboundEmail, ingest
from agent.rules import DocumentFacts, RuleSet, detect_injection, evaluate, load_rules
from agent.schemas import Classification, DisputeExtraction, InvoiceExtraction, RemittanceExtraction, Verification

log = logging.getLogger("ops_agent.process")


# --- helpers ---------------------------------------------------------------------------------------------

def _parties(session: Session) -> tuple[list[Party], list[Party]]:
    sup = [Party(s.id, s.name, tuple(s.identifier_patterns)) for s in session.scalars(select(Supplier))]
    cus = [Party(c.id, c.name, tuple(c.identifier_patterns)) for c in session.scalars(select(Customer))]
    return sup, cus


def party_name(session: Session, kind: str | None, pid: int | None) -> str | None:
    if pid is None:
        return None
    model = Supplier if kind == "supplier" else Customer
    obj = session.get(model, pid)
    return obj.name if obj else None


def _record(session: Session, doc: Document, call: LLMCall) -> None:
    session.add(Decision(document_id=doc.id, step=call.step, input=call.input_summary,
                         output=json.loads(call.output.model_dump_json()), model=call.model,
                         tokens_in=call.tokens_in, tokens_out=call.tokens_out, cost_usd=call.cost,
                         duration_ms=call.duration_ms))


def _record_plain(session: Session, doc: Document, step: str, output: dict[str, Any], duration_ms: int = 0) -> None:
    session.add(Decision(document_id=doc.id, step=step, input=None, output=output, model=None,
                         tokens_in=0, tokens_out=0, cost_usd=Decimal(0), duration_ms=duration_ms))


def _jsonable(model) -> dict[str, Any]:
    return json.loads(model.model_dump_json())


# --- the pipeline ------------------------------------------------------------------------------------------

def process_document(session: Session, doc: Document, llm: LLM, rules: RuleSet) -> Document:
    if doc.status == "ignore":  # decided at intake (duplicate / ignored sender)
        _record_plain(session, doc, "rules", {"status": doc.status, "reason": doc.reason, "decided_at": "intake"})
        return doc

    email: Email = doc.email
    header_text = f"From: {email.from_addr}\nSubject: {email.subject}\n\n{email.body_text or ''}"
    full_text = header_text if doc.filename == "email-body.txt" else f"{header_text}\n\n--- attachment {doc.filename} ---\n{doc.source_text or ''}"
    suppliers, customers = _parties(session)

    # 1. injection guard — on everything the outside party wrote
    injection = detect_injection(full_text)

    facts = DocumentFacts(doc_type=None, match=None, unreadable=doc.read_error is not None, injection=injection,
                          text_for_keywords=full_text)  # type: ignore[arg-type]

    # 2. classify (skip the model for unreadable attachments — there is nothing to read)
    if doc.read_error:
        doc.doc_type = "unknown"
        facts.match = match_any(header_text, suppliers, customers, prefer="supplier")
        _record_plain(session, doc, "classify", {"doc_type": "unknown", "rationale": f"unreadable: {doc.read_error}"})
    else:
        c = llm.classify(doc.source_text or "", {"from": email.from_addr, "subject": email.subject or "", "filename": doc.filename})
        _record(session, doc, c)
        cls: Classification = c.output  # type: ignore[assignment]
        doc.doc_type = cls.doc_type
        facts.classifier_flagged_injection = cls.instruction_like_content
        kind = cls.party_kind or ("supplier" if cls.doc_type in ("supplier_invoice", "credit_note") else "customer")
        # 3. match — deterministic, on the email headers/body plus this document's text
        if cls.doc_type == "ignore":
            facts.match = match_any(full_text, suppliers, customers)
        else:
            facts.match = match_party(kind, full_text, suppliers if kind == "supplier" else customers)
    facts.doc_type = doc.doc_type
    doc.party_kind = facts.match.party_kind
    doc.party_id = facts.match.party_id
    _record_plain(session, doc, "match", _jsonable(facts.match))

    # 4–5. extract + verify, only when we know what it is and who it is from
    if facts.match.status == "matched" and doc.doc_type in ("supplier_invoice", "customer_dispute", "remittance") and not injection:
        text = doc.source_text or ""
        if doc.doc_type == "supplier_invoice":
            e = llm.extract_invoice(text)
        elif doc.doc_type == "customer_dispute":
            e = llm.extract_dispute(text)
        else:
            e = llm.extract_remittance(text)
        _record(session, doc, e)
        extracted = _jsonable(e.output)
        doc.extracted = extracted

        v = llm.verify(text, extracted)
        _record(session, doc, v)
        ver: Verification = v.output  # type: ignore[assignment]
        doc.verify_result = _jsonable(ver)
        doc.confidence = _confidence(doc.doc_type, e.output, ver)
        facts.extracted = extracted
        facts.confidence = doc.confidence

        # 6. re-issued invoice? same supplier + same invoice number on an earlier, non-ignored document
        if doc.doc_type == "supplier_invoice":
            sup = session.get(Supplier, doc.party_id)
            facts.po_amount = sup.po_amount if sup else None
            facts.bank_last4_on_file = sup.iban_last4 if sup else None
            inv_no = extracted.get("invoice_number")
            if inv_no:
                prior = session.scalar(
                    select(Document.id).where(Document.id != doc.id, Document.party_kind == "supplier",
                                              Document.party_id == doc.party_id, Document.status != "ignore",
                                              Document.extracted["invoice_number"].astext == inv_no)
                    .order_by(Document.id).limit(1)
                )
                facts.duplicate_invoice_doc_id = prior
    elif injection and facts.match.status == "matched" and doc.doc_type == "supplier_invoice":
        # still extract so the page can show "the invoice itself looked fine — the email tried to steer us"
        e = llm.extract_invoice(doc.source_text or "")
        _record(session, doc, e)
        doc.extracted = _jsonable(e.output)
        facts.extracted = doc.extracted

    # 7. rules
    outcome = evaluate(facts, rules)
    doc.status, doc.reason = outcome.status, outcome.reason
    _record_plain(session, doc, "rules", _jsonable(outcome))

    # 8. draft reply for disputes we could attribute (held ones too — the human may want to send it)
    if doc.doc_type == "customer_dispute" and facts.match.status == "matched" and outcome.status in ("auto_approved", "held", "shadow") and not injection:
        claim = (doc.extracted or {}).get("claim_summary") or (email.subject or "")
        d = llm.draft_reply(doc.source_text or "", facts.match.party_name or "", claim)
        _record(session, doc, d)
        doc.draft_reply = f"Subject: {d.output.subject}\n\n{d.output.body}"  # type: ignore[union-attr]

    session.flush()
    return doc


def _confidence(doc_type: str, extracted, ver: Verification) -> Decimal:
    if not ver.all_fields_present:
        return Decimal("0.500")
    if isinstance(extracted, InvoiceExtraction) and (extracted.invoice_number is None or extracted.total is None):
        return Decimal("0.600")
    if isinstance(extracted, DisputeExtraction) and not extracted.claim_summary:
        return Decimal("0.700")
    if isinstance(extracted, RemittanceExtraction) and extracted.amount is None:
        return Decimal("0.600")
    return Decimal("0.950")


def process_email(session: Session, email: Email, llm: LLM | None = None, rules: RuleSet | None = None) -> list[Document]:
    llm = llm or get_llm()
    rules = rules or load_rules(session)
    docs = sorted(email.documents, key=lambda d: d.id)
    for doc in docs:
        try:
            process_document(session, doc, llm, rules)
        except Exception as exc:  # noqa: BLE001 — one bad document must not block the rest of the inbox
            log.exception("process: document #%s failed", doc.id)
            doc.status, doc.reason = "failed", f"pipeline error: {type(exc).__name__}: {str(exc)[:300]}"
            _record_plain(session, doc, "rules", {"status": "failed", "reason": doc.reason})
    email.ledger_status = "ignored" if docs and all(d.status == "ignore" for d in docs) else "processed"
    session.flush()
    return docs


def handle_inbound(session: Session, inbound: InboundEmail, llm: LLM | None = None) -> tuple[Email, list[Document]]:
    """Intake + process in one go. Used by the Gmail poller, /inject and the tests."""
    rules = load_rules(session)
    email, created = ingest(session, inbound, rules)
    if not created:
        return email, sorted(email.documents, key=lambda d: d.id)
    return email, process_email(session, email, llm, rules)
