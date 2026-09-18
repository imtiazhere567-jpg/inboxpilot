"""Actions — push an approved decision into the downstream systems (the former n8n 03-actions flow).

Triggered for status auto_approved (agent) or approved (human). Held items get a one-line Slack notice.

Idempotent: before calling a system we look for an existing `actions` row with status=ok for (document, system);
if one exists we reuse its external id. So a retry after a partial failure only redoes what actually failed, and
replaying never creates a second bill/ticket/message.

Not configured  -> action recorded as simulated (external id sim-…), demo still shows the flow.
Simulated outage (rules.simulate_outage) -> IntegrationError -> action row status=failed, document status=failed.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.config import get_settings
from agent.integrations.base import IntegrationError, SimulatedOutage, simulated_id
from agent.integrations.hubspot import HubSpotClient
from agent.integrations.qbo import QBOClient
from agent.integrations.slack import SlackClient
from agent.models import Action, Customer, Document, Supplier
from agent.rules import RuleSet, load_rules

log = logging.getLogger("ops_agent.actions")

_clients: dict[str, object] = {}


def _client(name: str):
    if name not in _clients:
        _clients[name] = {"slack": SlackClient, "hubspot": HubSpotClient, "qbo": QBOClient}[name]()
    return _clients[name]


def reset_clients() -> None:
    _clients.clear()


def _money(v) -> str:
    try:
        return f"£{Decimal(str(v)):,.2f}"
    except Exception:  # noqa: BLE001
        return "£?"


def _existing_ok(session: Session, doc: Document, system: str) -> Action | None:
    return session.scalar(select(Action).where(Action.document_id == doc.id, Action.system == system, Action.status == "ok"))


def _run(session: Session, doc: Document, system: str, rules: RuleSet, payload: dict, fn) -> Action:
    """Execute one action idempotently. fn() -> (external_id, result_dict). Raises IntegrationError on failure."""
    prior = _existing_ok(session, doc, system)
    if prior is not None:
        return prior
    client = _client(system)
    action = Action(document_id=doc.id, system=system, payload=payload)
    try:
        if rules.simulate_outage == system:
            raise SimulatedOutage(system)
        if not getattr(client, "configured", False):
            action.external_id, action.result = simulated_id(system), {"simulated": True, "note": f"{system} not configured — action simulated"}
        else:
            action.external_id, action.result = fn(client)
        action.status = "ok"
    except IntegrationError as exc:
        action.status, action.error = "failed", str(exc)[:500]
        session.add(action)
        session.flush()
        raise
    session.add(action)
    session.flush()
    return action


def execute_actions(session: Session, doc: Document, rules: RuleSet | None = None) -> Document:
    """Run every downstream action for a document. Sets status executed | failed. Safe to call again."""
    rules = rules or load_rules(session)
    email = doc.email
    party = _party(session, doc)
    party_name = party.name if party else "unknown"
    ex = doc.extracted or {}
    ref = ex.get("invoice_number") or ex.get("order_ref") or (email.subject or "")[:40]
    base_url = get_settings().app_base_url.rstrip("/")
    review_link = f"{base_url}/inbox#doc-{doc.id}"

    try:
        if doc.status in ("held",):
            _run(session, doc, "slack", rules, {"kind": "held"},
                 lambda c: (c.post(f"⏸ held · {ref} · {party_name} · {doc.reason} · review → {review_link}"), {"posted": True}))
            session.flush()
            return doc

        if doc.status not in ("auto_approved", "approved", "failed"):
            return doc

        how = "auto" if doc.status == "auto_approved" or _was_auto(doc) else "human-approved"
        summary_ids: list[str] = []

        if doc.doc_type == "supplier_invoice":
            total = Decimal(str(ex.get("total") or "0"))
            a = _run(session, doc, "qbo", rules, {"vendor": party_name, "amount": str(total), "invoice_number": ex.get("invoice_number")},
                     lambda c: _qbo_bill(session, c, party, ex, total, email.subject or ""))
            summary_ids.append(f"QuickBooks bill #{a.external_id}")
            _run(session, doc, "slack", rules, {"kind": "executed"},
                 lambda c: (c.post(f"✓ {ref} · {party_name} · {_money(total)} · {how} · {' · '.join(summary_ids)}"), {"posted": True}))

        elif doc.doc_type == "customer_dispute":
            a = _run(session, doc, "hubspot", rules, {"company": party_name, "subject": email.subject},
                     lambda c: _hubspot_ticket(session, c, party, doc, email.subject or "Customer dispute"))
            summary_ids.append(f"HubSpot ticket #{a.external_id}")
            amt = ex.get("requested_refund_amount")
            _run(session, doc, "slack", rules, {"kind": "executed"},
                 lambda c: (c.post(f"✓ dispute · {ref} · {party_name}{' · ' + _money(amt) if amt else ''} · {how} · {' · '.join(summary_ids)} · reply drafted (not sent)"), {"posted": True}))

        elif doc.doc_type == "remittance":
            _run(session, doc, "slack", rules, {"kind": "remittance"},
                 lambda c: (c.post(f"💷 payment received · {party_name} · {_money(ex.get('amount'))} · refs {', '.join(ex.get('invoice_refs') or []) or '—'}"), {"posted": True}))

        else:
            _run(session, doc, "slack", rules, {"kind": "executed"},
                 lambda c: (c.post(f"✓ {doc.doc_type} · {ref} · {party_name} · {how}"), {"posted": True}))

        doc.status = "executed"
        doc.reason = (doc.reason or "").split(" · failed:")[0]
    except IntegrationError as exc:
        doc.status = "failed"
        base_reason = (doc.reason or "").split(" · failed:")[0]
        doc.reason = f"{base_reason} · failed: {exc}"
        log.warning("actions: document #%s failed: %s", doc.id, exc)
    session.flush()
    return doc


def send_reply(session: Session, doc: Document, subject: str, body: str, rules: RuleSet | None = None) -> Action:
    """Email the (edited) draft reply to the customer. Only ever called from a human action. Idempotent per document."""
    from agent.integrations.gmail import GmailClient

    rules = rules or load_rules(session)
    settings = get_settings()
    party = _party(session, doc)
    to = (getattr(party, "contact_email", None) or doc.email.from_addr) if party else doc.email.from_addr
    doc.draft_reply = f"Subject: {subject}\n\n{body}"
    prior = _existing_ok(session, doc, "email")
    if prior is not None:
        return prior
    action = Action(document_id=doc.id, system="email", payload={"to": to, "subject": subject})
    g = GmailClient()
    live_send = settings.app_mode == "live" and g.configured
    try:
        if rules.simulate_outage == "email":
            raise SimulatedOutage("email")
        if live_send:
            mid = g.send_reply(to, subject, body, in_reply_to=doc.email.gmail_message_id)
            action.external_id, action.result = mid, {"to": to, "sent": True}
        else:
            action.external_id, action.result = simulated_id("email"), {"to": to, "simulated": True,
                                                                          "note": "demo mode / Gmail not connected — reply recorded, not sent"}
        action.status = "ok"
    except IntegrationError as exc:
        action.status, action.error = "failed", str(exc)[:500]
    session.add(action)
    session.flush()
    return action


def _was_auto(doc: Document) -> bool:
    return not any(n.action == "approve" for n in doc.review_notes)


def _party(session: Session, doc: Document):
    if doc.party_id is None:
        return None
    return session.get(Supplier if doc.party_kind == "supplier" else Customer, doc.party_id)


def _qbo_bill(session: Session, client: QBOClient, supplier: Supplier | None, ex: dict, total: Decimal, subject: str):
    name = supplier.name if supplier else "Unknown supplier"
    vendor_id = client.ensure_vendor(name, supplier.qbo_vendor_id if supplier else None)
    if supplier and not supplier.qbo_vendor_id:
        supplier.qbo_vendor_id = vendor_id
        session.flush()
    memo = f"{ex.get('invoice_number') or ''} — {subject}".strip(" —")
    bill_id = client.create_bill(vendor_id, name, total, ex.get("invoice_number"), ex.get("due_date"), memo)
    return bill_id, {"vendor_id": vendor_id, "url": client.bill_url(bill_id)}


def _hubspot_ticket(session: Session, client: HubSpotClient, customer: Customer | None, doc: Document, subject: str):
    name = customer.name if customer else "Unknown customer"
    company_id = client.ensure_company(name, customer.hubspot_company_id if customer else None)
    if customer and not customer.hubspot_company_id:
        customer.hubspot_company_id = company_id
        session.flush()
    ex = doc.extracted or {}
    content = (f"Agent summary: {ex.get('claim_summary') or ''}\n"
               f"Requested: {_money(ex.get('requested_refund_amount')) if ex.get('requested_refund_amount') else 'no amount'}\n"
               f"Reference: {ex.get('order_ref') or '—'}\n\n--- Draft reply (NOT sent) ---\n{doc.draft_reply or ''}")
    priority = "HIGH" if ex.get("tone") in ("angry", "legal_threat") else "MEDIUM"
    ticket_id = client.create_ticket(subject, content, company_id, priority)
    return ticket_id, {"company_id": company_id, "url": client.ticket_url(ticket_id)}


def delete_external_objects(session: Session) -> dict[str, int]:
    """Reset helper: best-effort removal of everything previous runs created in the sandboxes."""
    counts = {"qbo": 0, "hubspot": 0, "slack": 0}
    for a in session.scalars(select(Action).where(Action.status == "ok")):
        if not a.external_id or (a.result or {}).get("simulated"):
            continue
        try:
            _client(a.system).delete(a.external_id)
            counts[a.system] = counts.get(a.system, 0) + 1
        except Exception as exc:  # noqa: BLE001
            log.warning("cleanup %s %s failed: %s", a.system, a.external_id, exc)
    return counts
