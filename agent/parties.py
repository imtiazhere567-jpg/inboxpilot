"""Suppliers & customers directory: list / create / update / delete, suggestions from a held document,
"add from this document + reprocess", and import from QuickBooks vendors / HubSpot companies."""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.models import Customer, Document, Supplier

log = logging.getLogger("ops_agent.parties")

MODEL = {"supplier": Supplier, "customer": Customer}
_PREFIX_RE = re.compile(r"\b([A-Z]{2,5})-\d{3,6}\b")
_INTERNAL_DOMAINS = ("northwindfacilities.co.uk",)
_GENERIC_DOMAINS = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.uk", "icloud.com", "btinternet.com")


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower().strip() if "@" in addr else ""


def identifiers_from_emails(raw: list[str] | str) -> list[str]:
    """Turn email addresses into identifiers.
    company address  accounts@acmesupplies.co.uk -> acmesupplies.co.uk  (any sender at that domain matches)
    generic mailbox  jas.patel@gmail.com          -> jas.patel@gmail.com   (only that exact address matches)
    a bare domain is accepted as-is."""
    items = raw if isinstance(raw, list) else re.split(r"[|,;\s]+", raw or "")
    out: list[str] = []
    for p in items:
        p = p.strip().lower().strip("<>")
        if not p:
            continue
        if "@" in p:
            dom = p.rsplit("@", 1)[-1]
            ident = p if dom in _GENERIC_DOMAINS else dom
        else:
            ident = p
        if ident not in out:
            out.append(ident)
    return out


def _clean_patterns(raw: list[str] | str, prefixes: list[str] | str | None = None) -> list[str]:
    out = identifiers_from_emails(raw)
    pitems = prefixes if isinstance(prefixes, list) else re.split(r"[|,;\s]+", prefixes or "")
    for p in pitems:
        p = p.strip().upper()
        if not p:
            continue
        if not p.endswith("-"):
            p += "-"
        if p not in out:
            out.append(p)
    return out


def _doc_counts(session: Session, kind: str) -> dict[int, dict[str, int]]:
    rows = session.execute(
        select(Document.party_id, Document.status, func.count(Document.id))
        .where(Document.party_kind == kind, Document.party_id.isnot(None))
        .group_by(Document.party_id, Document.status)
    ).all()
    out: dict[int, dict[str, int]] = {}
    for pid, status, n in rows:
        e = out.setdefault(pid, {"total": 0, "held": 0})
        e["total"] += n
        if status == "held":
            e["held"] += n
    return out


def _to_dict(kind: str, obj, counts: dict[int, dict[str, int]]) -> dict[str, Any]:
    c = counts.get(obj.id, {"total": 0, "held": 0})
    pats = list(obj.identifier_patterns or [])
    d = {"id": obj.id, "kind": kind, "name": obj.name, "identifier_patterns": pats,
         "emails": [p for p in pats if not p.endswith("-")], "prefixes": [p for p in pats if p.endswith("-")],
         "documents": c["total"], "held": c["held"]}
    if kind == "supplier":
        d.update(po_amount=float(obj.po_amount) if obj.po_amount is not None else None, qbo_vendor_id=obj.qbo_vendor_id, iban_last4=obj.iban_last4)
    else:
        d.update(contact_email=obj.contact_email, hubspot_company_id=obj.hubspot_company_id)
    return d


def list_parties(session: Session, kind: str) -> list[dict[str, Any]]:
    model = MODEL[kind]
    counts = _doc_counts(session, kind)
    return [_to_dict(kind, o, counts) for o in session.scalars(select(model).order_by(model.name))]


def upsert_party(session: Session, kind: str, data: dict[str, Any], party_id: int | None = None):
    model = MODEL[kind]
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    patterns = _clean_patterns(data.get("identifier_patterns") or [], data.get("reference_prefix") or "")
    if not patterns:
        raise ValueError("at least one email address is required (e.g. accounts@acmesupplies.co.uk)")
    obj = session.get(model, party_id) if party_id else None
    if party_id and obj is None:
        raise LookupError("no such party")
    if obj is None:
        dup = session.scalar(select(model).where(func.lower(model.name) == name.lower()))
        if dup is not None:
            raise ValueError(f"{kind} '{dup.name}' already exists (#{dup.id})")
        obj = model(name=name)
    obj.name = name
    obj.identifier_patterns = patterns
    if kind == "supplier":
        po = data.get("po_amount")
        obj.po_amount = Decimal(str(po)) if po not in (None, "", "null") else None
        if "qbo_vendor_id" in data:
            obj.qbo_vendor_id = (data.get("qbo_vendor_id") or None)
        if "iban_last4" in data:
            obj.iban_last4 = (data.get("iban_last4") or None)
    else:
        if "contact_email" in data:
            obj.contact_email = (data.get("contact_email") or None)
        if "hubspot_company_id" in data:
            obj.hubspot_company_id = (data.get("hubspot_company_id") or None)
    session.add(obj)
    session.flush()
    return obj


def party_history(session: Session, kind: str, party_id: int) -> dict[str, Any]:
    """Every document attributed to this party (all runs), newest first, plus totals."""
    from sqlalchemy.orm import selectinload

    from agent.models import Email

    obj = session.get(MODEL[kind], party_id)
    if obj is None:
        raise LookupError("no such party")
    docs = session.scalars(
        select(Document).options(selectinload(Document.email), selectinload(Document.actions), selectinload(Document.review_notes))
        .join(Email).where(Document.party_kind == kind, Document.party_id == party_id).order_by(Email.received_at.desc(), Document.id.desc())
    ).all()
    rows, total = [], Decimal(0)
    for d in docs:
        ex = d.extracted or {}
        amount = ex.get("total") or ex.get("requested_refund_amount") or ex.get("amount")
        if amount and d.status not in ("ignore", "rejected"):
            total += Decimal(str(amount))
        rows.append({
            "document_id": d.id, "received_at": d.email.received_at, "subject": d.email.subject, "filename": d.filename,
            "doc_type": d.doc_type, "ref": ex.get("invoice_number") or ex.get("order_ref"), "amount": amount,
            "status": d.status, "reason": d.reason, "human": any(n.action in ("approve", "reject") for n in d.review_notes),
            "external": [f"{a.system}:{a.external_id}" for a in d.actions if a.status == "ok" and a.external_id],
        })
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"party": {"id": obj.id, "kind": kind, "name": obj.name}, "documents": rows, "count": len(rows),
            "total_amount": float(total), "by_status": by_status,
            "first_seen": rows[-1]["received_at"] if rows else None, "last_seen": rows[0]["received_at"] if rows else None}


def delete_party(session: Session, kind: str, party_id: int) -> None:
    obj = session.get(MODEL[kind], party_id)
    if obj is None:
        raise LookupError("no such party")
    session.delete(obj)
    session.flush()


# --- suggestion from a held document -----------------------------------------------------------------------------

def suggest_from_document(session: Session, doc: Document) -> dict[str, Any]:
    """Guess name + identifiers for the party a held document came from."""
    email = doc.email
    ex = doc.extracted or {}
    kind = "supplier" if doc.doc_type in ("supplier_invoice", "credit_note", None, "unknown") else "customer"
    text = doc.source_text or ""
    emails: list[str] = []
    dom = _domain(email.from_addr)
    if dom and dom not in _INTERNAL_DOMAINS:
        emails.append(email.from_addr.lower())
    for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        addr = m.group(0).lower()
        d = addr.rsplit("@", 1)[-1]
        if addr not in emails and d not in _INTERNAL_DOMAINS and len(emails) < 3:
            emails.append(addr)
    ref = ex.get("invoice_number") or ex.get("order_ref") or ""
    m = _PREFIX_RE.search(ref) or _PREFIX_RE.search(email.subject or "") or _PREFIX_RE.search(text[:600])
    prefix = m.group(1).upper() + "-" if m else ""
    patterns = _clean_patterns(emails, prefix)
    name = (ex.get("supplier_name_on_document") or ex.get("customer_name_on_document") or "").strip()
    if not name:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        name = first if 3 < len(first) < 60 and "@" not in first and not first.lower().startswith(("subject:", "from:", "invoice")) else ""
    if not name and dom:
        name = dom.split(".")[0].replace("-", " ").title()
    po = ex.get("total") if kind == "supplier" else None
    return {"kind": kind, "name": name, "identifier_patterns": patterns, "emails": emails, "reference_prefix": prefix, "po_amount": po,
            "contact_email": email.from_addr if kind == "customer" else None,
            "why": f"from {email.from_addr}" + (f", reference {ref}" if ref else "")}


def reprocess_document(session: Session, doc: Document) -> Document:
    """Wipe the decision fields and run the pipeline again (decisions are appended, so the audit trail is kept)."""
    from agent.llm import get_llm
    from agent.pipeline.actions import execute_actions
    from agent.pipeline.process import _record_plain, process_document
    from agent.rules import load_rules

    _record_plain(session, doc, "reprocess", {"previous_status": doc.status, "previous_reason": doc.reason})
    doc.doc_type = None
    doc.party_kind = None
    doc.party_id = None
    doc.extracted = None
    doc.confidence = None
    doc.verify_result = None
    doc.draft_reply = None
    doc.status = "received"
    doc.reason = None
    session.flush()
    rules = load_rules(session, force=True)
    process_document(session, doc, get_llm(), rules)
    if doc.status in ("auto_approved", "held"):
        execute_actions(session, doc, rules)
    session.flush()
    return doc


# --- import from the real systems ----------------------------------------------------------------------------------

def import_from_qbo(session: Session) -> dict[str, int]:
    from agent.integrations.qbo import QBOClient
    from quickbooks.objects.vendor import Vendor

    c = QBOClient()
    if not c.configured:
        raise LookupError("QuickBooks is not connected — add it in Settings first")
    created = updated = 0
    for v in Vendor.all(qb=c._qb, max_results=500):
        name = (v.DisplayName or v.CompanyName or "").strip()
        if not name:
            continue
        email = getattr(getattr(v, "PrimaryEmailAddr", None), "Address", None) or ""
        patterns = [d for d in [_domain(email)] if d]
        obj = session.scalar(select(Supplier).where(func.lower(Supplier.name) == name.lower()))
        if obj is None:
            session.add(Supplier(name=name, identifier_patterns=patterns, qbo_vendor_id=str(v.Id)))
            created += 1
        else:
            obj.qbo_vendor_id = obj.qbo_vendor_id or str(v.Id)
            for p in patterns:
                if p not in (obj.identifier_patterns or []):
                    obj.identifier_patterns = [*obj.identifier_patterns, p]
            updated += 1
    session.flush()
    return {"created": created, "updated": updated}


def import_from_hubspot(session: Session) -> dict[str, int]:
    from agent.integrations.hubspot import HubSpotClient

    c = HubSpotClient()
    if not c.configured:
        raise LookupError("HubSpot is not connected — add it in Settings first")
    created = updated = 0
    after = None
    while True:
        page = c._client.crm.companies.basic_api.get_page(limit=100, after=after, properties=["name", "domain"])
        for co in page.results:
            props = co.properties or {}
            name = (props.get("name") or "").strip()
            if not name:
                continue
            patterns = [d for d in [(props.get("domain") or "").lower().strip()] if d]
            obj = session.scalar(select(Customer).where(func.lower(Customer.name) == name.lower()))
            if obj is None:
                session.add(Customer(name=name, identifier_patterns=patterns, hubspot_company_id=str(co.id)))
                created += 1
            else:
                obj.hubspot_company_id = obj.hubspot_company_id or str(co.id)
                for p in patterns:
                    if p not in (obj.identifier_patterns or []):
                        obj.identifier_patterns = [*obj.identifier_patterns, p]
                updated += 1
        if not page.paging or not page.paging.next:
            break
        after = page.paging.next.after
    session.flush()
    return {"created": created, "updated": updated}
