"""Rules live in configuration, not code.

Source of truth is the Google Sheet `rules` tab (integrations/sheets.py, Phase 3). Every read is mirrored into
the `rules` table, which is also the fallback when the sheet is not configured or unreachable. Values are
cached for RULES_CACHE_SECONDS.

`evaluate()` is the single place that turns facts about a document into a status + human-readable reason.
The order of checks is deliberate: safety first (injection), then readability, then identity, then money.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.config import get_settings
from agent.models import Rule
from agent.schemas import MatchResult, RuleOutcome

log = logging.getLogger("ops_agent.rules")

DEFAULTS: dict[str, str] = {
    "invoice_auto_approve_max": "2000",
    "invoice_tolerance_pct": "5",
    "refund_auto_max": "200",
    "escalate_keywords": "legal,solicitor,lawsuit,ombudsman",
    "ignore_sender_domains": "mailchimp.com,newsletter.",
    "min_confidence_auto": "0.85",
    "shadow_mode": "false",
    "simulate_outage": "none",
}


@dataclass(frozen=True)
class RuleSet:
    invoice_auto_approve_max: Decimal
    invoice_tolerance_pct: Decimal
    refund_auto_max: Decimal
    escalate_keywords: tuple[str, ...]
    ignore_sender_domains: tuple[str, ...]
    min_confidence_auto: Decimal
    shadow_mode: bool
    simulate_outage: str
    raw: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, m: dict[str, str]) -> "RuleSet":
        v = {**DEFAULTS, **{k: str(val) for k, val in m.items() if val is not None}}

        def csv(s: str) -> tuple[str, ...]:
            return tuple(x.strip().lower() for x in s.split(",") if x.strip())

        return cls(
            invoice_auto_approve_max=Decimal(v["invoice_auto_approve_max"]),
            invoice_tolerance_pct=Decimal(v["invoice_tolerance_pct"]),
            refund_auto_max=Decimal(v["refund_auto_max"]),
            escalate_keywords=csv(v["escalate_keywords"]),
            ignore_sender_domains=csv(v["ignore_sender_domains"]),
            min_confidence_auto=Decimal(v["min_confidence_auto"]),
            shadow_mode=v["shadow_mode"].strip().lower() in ("true", "1", "yes", "on"),
            simulate_outage=v["simulate_outage"].strip().lower() or "none",
            raw=v,
        )


# --- loading ---------------------------------------------------------------------------------------------

_cache: dict[str, Any] = {"at": 0.0, "rules": None}


def _read_db(session: Session) -> dict[str, str]:
    return {r.key: r.value for r in session.scalars(select(Rule))}


def _mirror_to_db(session: Session, values: dict[str, str]) -> None:
    for k, val in values.items():
        obj = session.get(Rule, k) or Rule(key=k, value=val)
        obj.value = val
        session.add(obj)
    session.flush()


def load_rules(session: Session, force: bool = False) -> RuleSet:
    """Sheet first (if configured), else DB. Cached."""
    settings = get_settings()
    now = time.monotonic()
    if not force and _cache["rules"] is not None and now - _cache["at"] < settings.rules_cache_seconds:
        return _cache["rules"]

    values: dict[str, str] | None = None
    if settings.integrations_configured["rules_sheet"]:
        try:
            from agent.integrations.sheets import read_rules_sheet  # Phase 3

            values = read_rules_sheet()
            _mirror_to_db(session, values)
        except Exception as exc:  # noqa: BLE001 — fall back, never break the pipeline on a sheet hiccup
            log.warning("rules sheet unavailable, using DB copy: %s", exc)
    if values is None:
        values = _read_db(session)
    rules = RuleSet.from_mapping(values)
    _cache.update(at=now, rules=rules)
    return rules


def invalidate_cache() -> None:
    _cache.update(at=0.0, rules=None)


def sender_is_ignored(from_addr: str, rules: RuleSet) -> str | None:
    domain = from_addr.rsplit("@", 1)[-1].lower()
    for needle in rules.ignore_sender_domains:
        if needle in domain:
            return needle
    return None


# --- injection guard (deterministic; the classifier's flag is a second, independent signal) ---------------

INJECTION_PATTERNS = [
    r"ignore (all |any )?(prior|previous|your|the) (rules|instructions)",
    r"disregard (all |any )?(prior|previous|your|the) (rules|instructions)",
    r"(system|assistant) (note|message|prompt)\s*(to|for)?\s*(the )?(ai|assistant|agent|model)",
    r"mark (this|the) (invoice|document|item)? ?(as )?approved",
    r"do not (hold|flag|escalate)",
    r"approve (this|it) (immediately|now|without)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> str | None:
    m = _INJECTION_RE.search(text)
    return m.group(0) if m else None


# --- evaluation -----------------------------------------------------------------------------------------

@dataclass
class DocumentFacts:
    doc_type: str | None
    match: MatchResult
    unreadable: bool = False
    injection: str | None = None
    classifier_flagged_injection: bool = False
    extracted: dict[str, Any] | None = None
    confidence: Decimal | None = None
    po_amount: Decimal | None = None
    duplicate_invoice_doc_id: int | None = None
    text_for_keywords: str = ""


def _money(x: Decimal) -> str:
    return f"£{x:,.2f}"


def evaluate(f: DocumentFacts, rules: RuleSet) -> RuleOutcome:
    out = _evaluate(f, rules)
    if rules.shadow_mode and out.status in ("auto_approved", "held"):
        return RuleOutcome(status="shadow", reason=f"shadow mode · would be {out.status} · {out.reason}",
                           triggered_rules=out.triggered_rules + ["shadow_mode"], would_be=out.status)
    return out


def _evaluate(f: DocumentFacts, rules: RuleSet) -> RuleOutcome:
    ex = f.extracted or {}

    if f.injection or f.classifier_flagged_injection:
        snippet = f.injection or "classifier flag"
        return RuleOutcome(status="held", reason=f"instruction-like content detected in email (\"{snippet}\") — not acted on",
                           triggered_rules=["injection_guard"])

    if f.doc_type == "ignore":
        return RuleOutcome(status="ignore", reason="not an invoice, dispute or remittance", triggered_rules=["classify"])

    if f.unreadable:
        return RuleOutcome(status="held", reason="could not read attachment", triggered_rules=["unreadable"])

    if f.doc_type in (None, "unknown"):
        return RuleOutcome(status="held", reason="could not determine document type", triggered_rules=["classify_unknown"])

    kind = f.match.party_kind or ("supplier" if f.doc_type == "supplier_invoice" else "customer")
    if f.match.status == "none":
        return RuleOutcome(status="held", reason=f"no {kind} match", triggered_rules=["match_none"])
    if f.match.status == "ambiguous":
        return RuleOutcome(status="held", reason=f"ambiguous {kind}: {' / '.join(f.match.candidates)}",
                           triggered_rules=["match_ambiguous"])
    if f.match.status == "name_only":
        return RuleOutcome(status="held", reason=f"matched by name only ({f.match.party_name}); confirm",
                           triggered_rules=["match_name_only"])

    triggered: list[str] = []

    if f.doc_type == "supplier_invoice":
        if f.duplicate_invoice_doc_id:
            return RuleOutcome(status="held", reason=f"possible duplicate: invoice number {ex.get('invoice_number')} "
                                                     f"already seen (doc #{f.duplicate_invoice_doc_id})",
                               triggered_rules=["duplicate_invoice_number"])
        total = _dec(ex.get("total"))
        if total is None:
            return RuleOutcome(status="held", reason="no total amount found on invoice", triggered_rules=["missing_total"])
        if total > rules.invoice_auto_approve_max:
            return RuleOutcome(status="held", reason=f"over threshold {_money(rules.invoice_auto_approve_max)} "
                                                     f"(invoice {_money(total)})", triggered_rules=["invoice_auto_approve_max"])
        if f.po_amount:
            diff_pct = abs(total - f.po_amount) / f.po_amount * 100
            if diff_pct > rules.invoice_tolerance_pct:
                return RuleOutcome(status="held", reason=f"amount {_money(total)} differs from PO {_money(f.po_amount)} "
                                                         f"by {diff_pct:.1f}% (limit {rules.invoice_tolerance_pct}%)",
                                   triggered_rules=["invoice_tolerance_pct"])
            triggered.append("invoice_tolerance_pct")
        triggered.append("invoice_auto_approve_max")

    elif f.doc_type == "customer_dispute":
        low = f.text_for_keywords.lower()
        for kw in rules.escalate_keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", low):
                return RuleOutcome(status="held", reason=f"escalate: legal language (\"{kw}\") — human to handle",
                                   triggered_rules=["escalate_keywords"])
        refund = _dec(ex.get("requested_refund_amount"))
        if refund is not None and refund > rules.refund_auto_max:
            return RuleOutcome(status="held", reason=f"refund request {_money(refund)} exceeds auto limit "
                                                     f"{_money(rules.refund_auto_max)}", triggered_rules=["refund_auto_max"])
        triggered.append("refund_auto_max")

    elif f.doc_type == "remittance":
        amount = _dec(ex.get("amount"))
        return RuleOutcome(status="auto_approved",
                           reason=f"remittance advice{' ' + _money(amount) if amount is not None else ''} — recorded, no financial action",
                           triggered_rules=["remittance"])

    elif f.doc_type == "credit_note":
        return RuleOutcome(status="held", reason="credit notes are always reviewed by a person", triggered_rules=["credit_note"])

    if f.confidence is not None and f.confidence < rules.min_confidence_auto:
        return RuleOutcome(status="held", reason=f"confidence {f.confidence:.2f} below minimum {rules.min_confidence_auto}",
                           triggered_rules=triggered + ["min_confidence_auto"])
    triggered.append("min_confidence_auto")

    return RuleOutcome(status="auto_approved", reason="within rules: " + ", ".join(triggered), triggered_rules=triggered)


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None
