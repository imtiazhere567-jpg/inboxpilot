"""Read models for the page: queue, review item, ledger, status. Pure queries — no side effects."""
from __future__ import annotations

import time

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from agent import events
from agent.config import get_settings
from agent.models import Action, AppState, Customer, Decision, Document, Email, ReviewNote, Run, Supplier
from agent.rules import load_rules
from agent.schemas import LedgerRow, ReviewItem, TimelineEvent

MANUAL_MINUTES_PER_DOCUMENT = {"supplier_invoice": 6, "customer_dispute": 9, "remittance": 2, "ignore": 1, "unknown": 4, "credit_note": 6}


def _party_names(session: Session) -> dict[tuple[str, int], str]:
    names: dict[tuple[str, int], str] = {}
    for s in session.scalars(select(Supplier)):
        names[("supplier", s.id)] = s.name
    for c in session.scalars(select(Customer)):
        names[("customer", c.id)] = c.name
    return names


def _current_run_id(session: Session) -> int | None:
    state = session.get(AppState, 1)
    return state.current_run_id if state else None


def _docs_query(session: Session, run_only: bool = True):
    q = select(Document).options(selectinload(Document.email), selectinload(Document.actions),
                                 selectinload(Document.review_notes), selectinload(Document.decisions))
    if run_only:
        rid = _current_run_id(session)
        if rid is not None:
            q = q.join(Email).where(Email.run_id == rid)
    return q.order_by(Document.id)


def _cost(doc: Document) -> float:
    return float(sum((d.cost_usd or Decimal(0)) for d in doc.decisions))


def _agent_ms(doc: Document) -> int:
    return int(sum((d.duration_ms or 0) for d in doc.decisions))


def _external_refs(doc: Document) -> list[str]:
    return [f"{a.system}:{a.external_id}" for a in doc.actions if a.status == "ok" and a.external_id]


def _snippet(doc: Document, limit: int = 1200) -> str | None:
    if not doc.source_text:
        return None
    t = doc.source_text.strip()
    return t if len(t) <= limit else t[:limit] + " …"


def _timeline(doc: Document) -> list[TimelineEvent]:
    ev = [TimelineEvent(step="received", at=doc.created_at)]
    for d in sorted(doc.decisions, key=lambda d: (d.at, d.id)):
        ev.append(TimelineEvent(step=d.step, at=d.at, duration_ms=d.duration_ms, model=d.model))
    for n in sorted(doc.review_notes, key=lambda n: n.at):
        ev.append(TimelineEvent(step=f"human:{n.action}", at=n.at))
    for a in sorted(doc.actions, key=lambda a: a.at):
        ev.append(TimelineEvent(step=f"{a.system}:{a.status}", at=a.at))
    return ev


def queue(session: Session) -> list[dict[str, Any]]:
    names = _party_names(session)
    out = []
    for doc in session.scalars(_docs_query(session)):
        ex = doc.extracted or {}
        out.append({
            "document_id": doc.id, "email_id": doc.email_id, "seed_no": doc.email.seed_no,
            "filename": doc.filename, "from_addr": doc.email.from_addr, "subject": doc.email.subject,
            "received_at": doc.email.received_at, "doc_type": doc.doc_type, "party_kind": doc.party_kind,
            "party_name": names.get((doc.party_kind, doc.party_id)) if doc.party_id else None,
            "status": doc.status, "reason": doc.reason,
            "confidence": float(doc.confidence) if doc.confidence is not None else None,
            "amount": ex.get("total") or ex.get("requested_refund_amount") or ex.get("amount"),
            "ref": ex.get("invoice_number") or ex.get("order_ref"),
            "duplicate_of": doc.duplicate_of, "cost_usd": round(_cost(doc), 5),
            "simulated": any((a.result or {}).get("simulated") for a in doc.actions),
            "human_override": any(n.action in ("approve", "reject") for n in doc.review_notes),
            "injection": any("injection_guard" in (d.output or {}).get("triggered_rules", []) for d in doc.decisions if d.step == "rules"),
            "updated_at": doc.updated_at,
        })
    return out


def review_item(session: Session, document_id: int) -> ReviewItem | None:
    doc = session.scalar(select(Document).options(selectinload(Document.email), selectinload(Document.actions),
                                                  selectinload(Document.review_notes), selectinload(Document.decisions))
                         .where(Document.id == document_id))
    if doc is None:
        return None
    names = _party_names(session)
    return ReviewItem(
        document_id=doc.id, email_id=doc.email_id, seed_no=doc.email.seed_no, filename=doc.filename,
        from_addr=doc.email.from_addr, subject=doc.email.subject, doc_type=doc.doc_type, party_kind=doc.party_kind,
        party_name=names.get((doc.party_kind, doc.party_id)) if doc.party_id else None,
        status=doc.status, reason=doc.reason, confidence=float(doc.confidence) if doc.confidence is not None else None,
        extracted=doc.extracted, verify_result=doc.verify_result, source_snippet=_snippet(doc),
        draft_reply=doc.draft_reply, duplicate_of=doc.duplicate_of,
        actions=[{"system": a.system, "status": a.status, "external_id": a.external_id, "error": a.error,
                  "result": a.result, "at": a.at.isoformat()} for a in sorted(doc.actions, key=lambda a: a.id)],
        timeline=_timeline(doc), cost_usd=round(_cost(doc), 5), created_at=doc.created_at, updated_at=doc.updated_at,
    )


def review_extras(session: Session, doc_id: int) -> dict[str, Any]:
    """Decision trail + notes for the review panel (kept out of ReviewItem to keep that schema stable)."""
    decisions = session.scalars(select(Decision).where(Decision.document_id == doc_id).order_by(Decision.id)).all()
    notes = session.scalars(select(ReviewNote).where(ReviewNote.document_id == doc_id).order_by(ReviewNote.id)).all()
    return {
        "decisions": [{"step": d.step, "model": d.model, "tokens_in": d.tokens_in, "tokens_out": d.tokens_out,
                       "cost_usd": float(d.cost_usd or 0), "duration_ms": d.duration_ms, "output": d.output, "at": d.at.isoformat()}
                      for d in decisions],
        "notes": [{"action": n.action, "note": n.note, "by": n.by, "at": n.at.isoformat()} for n in notes],
        "email_body": session.get(Document, doc_id).email.body_text,
    }


def ledger(session: Session, overrides_only: bool = False, all_runs: bool = False) -> list[LedgerRow]:
    names = _party_names(session)
    rows = []
    for doc in session.scalars(_docs_query(session, run_only=not all_runs)):
        override = any(n.action in ("approve", "reject") for n in doc.review_notes)
        if overrides_only and not override:
            continue
        rows.append(LedgerRow(
            document_id=doc.id, seed_no=doc.email.seed_no, received_at=doc.email.received_at, from_addr=doc.email.from_addr,
            subject=doc.email.subject, filename=doc.filename, doc_type=doc.doc_type,
            party_name=names.get((doc.party_kind, doc.party_id)) if doc.party_id else None,
            status=doc.status, reason=doc.reason, confidence=float(doc.confidence) if doc.confidence is not None else None,
            external_refs=_external_refs(doc), human_override=override, cost_usd=round(_cost(doc), 5),
        ))
    return rows


def ledger_csv(rows: list[LedgerRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["document_id", "seed", "received_at", "from", "subject", "file", "type", "party", "status", "reason",
                "confidence", "external_refs", "human_override", "cost_usd"])
    for r in rows:
        w.writerow([r.document_id, r.seed_no, r.received_at.isoformat(), r.from_addr, r.subject, r.filename, r.doc_type,
                    r.party_name, r.status, r.reason, r.confidence, " ".join(r.external_refs), r.human_override, r.cost_usd])
    return buf.getvalue()


def status(session: Session) -> dict[str, Any]:
    settings = get_settings()
    rules = load_rules(session)
    state = session.get(AppState, 1)
    run = session.get(Run, state.current_run_id) if state and state.current_run_id else None
    docs = session.scalars(_docs_query(session)).all()
    counts: dict[str, int] = {}
    for d in docs:
        counts[d.status] = counts.get(d.status, 0) + 1
    cost = sum(_cost(d) for d in docs)
    agent_ms = sum(_agent_ms(d) for d in docs)
    manual_min = sum(MANUAL_MINUTES_PER_DOCUMENT.get(d.doc_type or "unknown", 4) for d in docs if d.status != "ignore")
    now = datetime.now(timezone.utc)
    flags = events.flags()
    return {
        "version": events.version(),
        "run_id": run.id if run else None,
        "run_started_at": run.started_at.isoformat() if run else None,
        "mode": "shadow" if rules.shadow_mode else "live",
        "app_mode": settings.app_mode,
        "presentation": bool(settings.presentation_mode),
        "company": {"name": settings.company_name, "email": settings.company_email, "initials": settings.company_initials, "logo": settings.company_logo or None},
        "shadow_mode": rules.shadow_mode,
        "simulate_outage": rules.simulate_outage,
        "resetting": bool(flags.get("resetting")),
        "seeding": flags.get("seeding"),
        "last_error": flags.get("last_error"),
        "next_reset_at": state.next_reset_at.isoformat() if state and state.next_reset_at else None,
        "seconds_to_reset": max(0, int((state.next_reset_at - now).total_seconds())) if state and state.next_reset_at else None,
        "reset_idle_minutes": settings.reset_idle_minutes,
        "counts": counts,
        "documents": len(docs),
        "emails": session.scalar(select(func.count(Email.id)).where(Email.run_id == run.id)) if run else 0,
        "cost_usd": round(cost, 4),
        "agent_seconds": round(agent_ms / 1000, 1),
        "estimated_manual_minutes": manual_min,
        "human_reviews": sum(1 for d in docs if any(n.action in ("approve", "reject") for n in d.review_notes)),
        "integrations": settings.integrations_configured,
        "llm": "fake" if (settings.llm_fake or not settings.anthropic_api_key) else settings.claude_model_main,
        "rules": rules.raw,
        "rules_sheet_url": _sheet_url(),
        "snapshot_at": now.isoformat(),
    }


def _sheet_url() -> str | None:
    try:
        from agent.integrations.sheets import sheet_url

        return sheet_url()
    except Exception:  # noqa: BLE001
        return None


_last_touch = 0.0


def touch_interaction(session: Session) -> None:
    """Record that a person is using the page (defers the idle-aware reset). Written at most every 20 s so a page
    load does not cost a database write per request."""
    global _last_touch
    now = time.monotonic()
    if now - _last_touch < 20:
        return
    state = session.get(AppState, 1)
    if state:
        state.last_interaction_at = datetime.now(timezone.utc)
        _last_touch = now
