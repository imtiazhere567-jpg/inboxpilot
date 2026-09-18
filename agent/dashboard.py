"""Dashboard aggregates (today / last 7 days / top parties / attention / activity) and the AI-readable ledger context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from agent.models import Document
from agent.service import MANUAL_MINUTES_PER_DOCUMENT, _agent_ms, _cost, _docs_query, _party_names, status


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _money(v) -> str:
    return f"£{_dec(v):,.2f}"


def _is_money_doc(d: Document) -> bool:
    return d.status not in ("ignore", "rejected")


def _human_approved(d: Document) -> bool:
    return any(n.action == "approve" for n in d.review_notes)


def dashboard(session: Session, tz_offset_minutes: int = 0) -> dict[str, Any]:
    names = _party_names(session)
    docs = session.scalars(_docs_query(session)).all()
    now_utc = datetime.now(timezone.utc)
    today = (now_utc + timedelta(minutes=tz_offset_minutes)).date()

    def local_date(dt: datetime):
        # DB timestamps arrive in the session's zone; normalise to UTC before applying the browser offset
        return (dt.astimezone(timezone.utc) + timedelta(minutes=tz_offset_minutes)).date()

    def block(dd: list[Document], date) -> dict[str, Any]:
        inv = [d for d in dd if d.doc_type == "supplier_invoice" and _is_money_doc(d)]
        disp = [d for d in dd if d.doc_type == "customer_dispute" and _is_money_doc(d)]
        return {
            "date": date.isoformat(),
            "emails": len({d.email_id for d in dd}),
            "documents": len(dd),
            "invoices": len(inv),
            "invoices_total": float(sum(_dec((d.extracted or {}).get("total")) for d in inv)),
            "disputes": len(disp),
            "refunds_requested": float(sum(_dec((d.extracted or {}).get("requested_refund_amount")) for d in disp)),
            "remittances": sum(1 for d in dd if d.doc_type == "remittance"),
            "held": sum(1 for d in dd if d.status == "held"),
            "executed": sum(1 for d in dd if d.status == "executed"),
            "auto_executed": sum(1 for d in dd if d.status == "executed" and not _human_approved(d)),
            "failed": sum(1 for d in dd if d.status == "failed"),
            "ignored": sum(1 for d in dd if d.status == "ignore"),
            "cost_usd": round(sum(_cost(d) for d in dd), 4),
            "agent_seconds": round(sum(_agent_ms(d) for d in dd) / 1000, 1),
            "manual_minutes": sum(MANUAL_MINUTES_PER_DOCUMENT.get(d.doc_type or "unknown", 4) for d in dd if d.status != "ignore"),
        }

    today_docs = [d for d in docs if local_date(d.email.received_at) == today]
    days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        dd = [d for d in docs if local_date(d.email.received_at) == day]
        days.append({
            "date": day.isoformat(), "label": day.strftime("%a %d"), "received": len(dd),
            "auto": sum(1 for d in dd if d.status in ("executed", "auto_approved", "approved") and not _human_approved(d)),
            "held": sum(1 for d in dd if d.status in ("held", "rejected", "failed") or _human_approved(d)),
            "ignored": sum(1 for d in dd if d.status == "ignore"),
            "invoices_total": float(sum(_dec((d.extracted or {}).get("total")) for d in dd if d.doc_type == "supplier_invoice" and _is_money_doc(d))),
            "handled_total": float(sum(_dec((d.extracted or {}).get("total")) for d in dd if d.doc_type == "supplier_invoice" and d.status in ("executed", "auto_approved", "approved") and not _human_approved(d))),
            "waiting_total": float(sum(_dec((d.extracted or {}).get("total")) for d in dd if d.doc_type == "supplier_invoice" and (d.status in ("held", "failed") or _human_approved(d)))),
            "disputes": sum(1 for d in dd if d.doc_type == "customer_dispute" and _is_money_doc(d)),
            "disputes_handled": sum(1 for d in dd if d.doc_type == "customer_dispute" and d.status in ("executed", "auto_approved", "approved") and not _human_approved(d)),
            "disputes_waiting": sum(1 for d in dd if d.doc_type == "customer_dispute" and (d.status in ("held", "failed") or _human_approved(d))),
            "refunds_requested": float(sum(_dec((d.extracted or {}).get("requested_refund_amount")) for d in dd if d.doc_type == "customer_dispute" and _is_money_doc(d))),
        })

    sup: dict[str, dict] = {}
    cus: dict[str, dict] = {}
    for d in docs:
        if not (d.party_id and _is_money_doc(d)):
            continue
        ex = d.extracted or {}
        if d.doc_type == "supplier_invoice":
            n = names.get(("supplier", d.party_id), "?")
            e = sup.setdefault(n, {"name": n, "invoices": 0, "total": Decimal(0), "held": 0})
            e["invoices"] += 1
            e["total"] += _dec(ex.get("total"))
            e["held"] += int(d.status == "held")
        elif d.doc_type == "customer_dispute":
            n = names.get(("customer", d.party_id), "?")
            e = cus.setdefault(n, {"name": n, "disputes": 0, "requested": Decimal(0), "held": 0})
            e["disputes"] += 1
            e["requested"] += _dec(ex.get("requested_refund_amount"))
            e["held"] += int(d.status == "held")
    top_suppliers = sorted(sup.values(), key=lambda e: -e["total"])[:6]
    top_customers = sorted(cus.values(), key=lambda e: (-e["disputes"], -e["requested"]))[:6]
    for e in top_suppliers:
        e["total"] = float(e["total"])
    for e in top_customers:
        e["requested"] = float(e["requested"])

    attention = []
    for d in docs:
        if d.status in ("held", "failed"):
            ex = d.extracted or {}
            attention.append({
                "document_id": d.id, "seed_no": d.email.seed_no, "subject": d.email.subject, "status": d.status, "reason": d.reason,
                "party": names.get((d.party_kind, d.party_id)) if d.party_id else None, "doc_type": d.doc_type,
                "amount": ex.get("total") or ex.get("requested_refund_amount"), "received_at": d.email.received_at,
            })
    attention.sort(key=lambda a: (a["status"] != "failed", a["received_at"]))

    feed = []
    for d in docs:
        ex = d.extracted or {}
        label = ex.get("invoice_number") or ex.get("order_ref") or (d.email.subject or d.filename)
        party = names.get((d.party_kind, d.party_id)) if d.party_id else d.email.from_addr
        amt = ex.get("total") or ex.get("requested_refund_amount") or ex.get("amount")
        money = f" · {_money(amt)}" if amt else ""
        for dec in d.decisions:
            if dec.step == "rules":
                st = (dec.output or {}).get("status")
                icon = {"auto_approved": "✓", "held": "⏸", "ignore": "·", "shadow": "◌", "failed": "✗"}.get(st, "·")
                feed.append({"at": dec.at, "icon": icon, "kind": st, "document_id": d.id,
                             "text": f"{label} · {party}{money} · {(dec.output or {}).get('reason', '')}"})
        for a in d.actions:
            err = f" · {a.error}" if a.error else ""
            target = {"qbo": f"QuickBooks bill #{a.external_id}", "hubspot": f"HubSpot ticket #{a.external_id}", "slack": "Slack #ops-agent"}.get(a.system, a.system) if a.status == "ok" else a.system
            feed.append({"at": a.at, "icon": "→" if a.status == "ok" else "✗", "kind": "action" if a.status == "ok" else "failed",
                         "document_id": d.id, "text": f"{label} → {target}{err}"})
        for n in d.review_notes:
            feed.append({"at": n.at, "icon": "👤", "kind": "human", "document_id": d.id, "text": f"{label} · {n.action} by {n.by}: {n.note}"})
    feed.sort(key=lambda f: f["at"], reverse=True)

    return {
        "today": block(today_docs, today),
        "run": block(docs, today) | {"date": None},
        "days": days,
        "top_suppliers": top_suppliers,
        "top_customers": top_customers,
        "attention": attention[:12],
        "attention_total": len(attention),
        "feed": feed[:25],
        "snapshot_at": now_utc.isoformat(),
    }


def ledger_context_for_ai(session: Session, limit: int = 150) -> dict[str, Any]:
    """Compact, model-readable view of the current run for the 'Ask the agent' feature."""
    names = _party_names(session)
    rows = []
    for d in session.scalars(_docs_query(session)):
        ex = d.extracted or {}
        rows.append({
            "doc": d.id, "seed": d.email.seed_no, "received": d.email.received_at.astimezone(timezone.utc).isoformat(timespec="minutes"),
            "from": d.email.from_addr, "subject": d.email.subject, "file": d.filename, "type": d.doc_type,
            "party": names.get((d.party_kind, d.party_id)) if d.party_id else None, "status": d.status, "reason": d.reason,
            "amount": ex.get("total") or ex.get("requested_refund_amount") or ex.get("amount"),
            "ref": ex.get("invoice_number") or ex.get("order_ref"),
            "confidence": float(d.confidence) if d.confidence is not None else None,
            "human": [f"{n.action}: {n.note}" for n in d.review_notes] or None,
            "actions": [f"{a.system}:{a.status}" for a in d.actions] or None,
        })
    st = status(session)
    return {"now": datetime.now(timezone.utc).isoformat(timespec="minutes"), "documents": rows[-limit:],
            "summary": {k: st[k] for k in ("mode", "counts", "documents", "emails", "cost_usd", "estimated_manual_minutes", "human_reviews")}}
