"""Phase 2 contract: every seed email, run through intake + process, must land on its `expected` outcome.

Runs with FakeLLM by default (no network). With RUN_REAL_LLM=1 and ANTHROPIC_API_KEY set it uses Claude —
that run is the Phase 2 acceptance and its result is written to docs/scorecard.json for the under-the-hood page.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from agent.llm import get_llm
from agent.models import Decision, Document, Email
from agent.pipeline.process import handle_inbound, party_name
from agent.seed_loader import read_seed_emails, to_inbound

SCORECARD = Path(__file__).resolve().parent.parent / "docs" / "scorecard.json"
RUN_TAG = datetime.now(timezone.utc).strftime("t%H%M%S")


def _money_eq(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return Decimal(str(a)) == Decimal(str(b))


def check_document(session, seed, exp: dict, doc: Document, by_seed: dict[int, list[Document]]) -> list[str]:
    """Return a list of mismatch descriptions (empty = pass)."""
    problems = []
    if doc.doc_type != exp["doc_type"]:
        problems.append(f"doc_type {doc.doc_type!r} != {exp['doc_type']!r}")
    if doc.status != exp["status"]:
        problems.append(f"status {doc.status!r} != {exp['status']!r} (reason: {doc.reason})")
    if exp.get("reason_contains") and (doc.reason or "").lower().find(exp["reason_contains"].lower()) < 0:
        problems.append(f"reason {doc.reason!r} lacks {exp['reason_contains']!r}")
    got_party = party_name(session, doc.party_kind, doc.party_id)
    if exp.get("party") and got_party != exp["party"]:
        problems.append(f"party {got_party!r} != {exp['party']!r}")
    if exp.get("party_kind") and doc.party_kind != exp["party_kind"]:
        problems.append(f"party_kind {doc.party_kind!r} != {exp['party_kind']!r}")
    if exp.get("candidates"):
        rules_dec = [d for d in doc.decisions if d.step == "match"]
        cands = rules_dec[-1].output.get("candidates") if rules_dec else None
        if sorted(cands or []) != sorted(exp["candidates"]):
            problems.append(f"candidates {cands} != {exp['candidates']}")
    ex = doc.extracted or {}
    for k, v in (exp.get("extracted") or {}).items():
        if k in ("total", "requested_refund_amount", "amount"):
            if not _money_eq(ex.get(k), v):
                problems.append(f"extracted.{k} {ex.get(k)!r} != {v!r}")
        elif k == "invoice_number":
            if ex.get(k) != v:
                problems.append(f"extracted.{k} {ex.get(k)!r} != {v!r}")
    if exp.get("duplicate_of_seed"):
        orig_docs = by_seed.get(exp["duplicate_of_seed"], [])
        if not orig_docs or doc.duplicate_of != orig_docs[0].id:
            problems.append(f"duplicate_of {doc.duplicate_of} != doc of seed {exp['duplicate_of_seed']}")
    return problems


@pytest.mark.usefixtures("db")
def test_all_thirty_seed_emails(session):
    llm = get_llm()
    seeds = read_seed_emails()
    by_seed: dict[int, list[Document]] = {}
    rows = []
    failures = []

    for seed in seeds:
        email, docs = handle_inbound(session, to_inbound(seed, RUN_TAG), llm)
        session.commit()
        for d in docs:
            session.refresh(d)
        by_seed[seed["seed_no"]] = docs
        expected_docs = seed["expected"]["documents"]
        got_by_name = {d.filename: d for d in docs}

        if sorted(got_by_name) != sorted(e["filename"] for e in expected_docs):
            failures.append(f"#{seed['seed_no']}: documents {sorted(got_by_name)} != {sorted(e['filename'] for e in expected_docs)}")
            rows.append((seed["seed_no"], "-", "FAIL", "document set mismatch"))
            continue
        for exp in expected_docs:
            doc = got_by_name[exp["filename"]]
            problems = check_document(session, seed, exp, doc, by_seed)
            cost = session.scalar(select(func.coalesce(func.sum(Decision.cost_usd), 0)).where(Decision.document_id == doc.id))
            rows.append((seed["seed_no"], exp["filename"], "ok" if not problems else "FAIL",
                         f"{doc.status} · {doc.reason}" if not problems else "; ".join(problems), float(cost)))
            if problems:
                failures.append(f"#{seed['seed_no']} {exp['filename']}: " + "; ".join(problems))

    # --- report ---------------------------------------------------------------------------------------------
    total_cost = float(session.scalar(select(func.coalesce(func.sum(Decision.cost_usd), 0))) or 0)
    model = getattr(llm, "model_main", getattr(llm, "model", "fake"))
    print(f"\n{'#':>3} {'document':32} {'result':6} outcome")
    for r in rows:
        print(f"{r[0]:>3} {str(r[1])[:32]:32} {r[2]:6} {str(r[3])[:110]}")
    passed = sum(1 for r in rows if r[2] == "ok")
    print(f"\n{passed}/{len(rows)} documents matched expected · model={model} · total cost ${total_cost:.4f}")

    if os.environ.get("RUN_REAL_LLM") == "1" or not SCORECARD.exists():
        SCORECARD.parent.mkdir(exist_ok=True)
        SCORECARD.write_text(json.dumps({
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "documents": len(rows), "passed": passed,
            "total_cost_usd": round(total_cost, 4),
            "rows": [{"seed": r[0], "document": r[1], "result": r[2], "outcome": r[3], "cost_usd": (round(r[4], 5) if len(r) > 4 else 0)} for r in rows],
        }, indent=2) + "\n", encoding="utf-8")

    assert not failures, "\n".join(failures)


@pytest.mark.usefixtures("db")
def test_replaying_an_email_changes_nothing(session):
    """Non-negotiable #8: idempotent. Same message id twice -> same email row, same document count."""
    seed = read_seed_emails()[0]
    before_docs = session.scalar(select(func.count(Document.id)))
    before_emails = session.scalar(select(func.count(Email.id)))
    email, docs = handle_inbound(session, to_inbound(seed, RUN_TAG), get_llm())
    session.commit()
    assert session.scalar(select(func.count(Document.id))) == before_docs
    assert session.scalar(select(func.count(Email.id))) == before_emails
    assert email.gmail_message_id == f"seed-01-{RUN_TAG}"


@pytest.mark.usefixtures("db")
def test_every_document_has_a_decision_trail(session):
    for doc in session.scalars(select(Document)):
        steps = [d.step for d in doc.decisions]
        assert "rules" in steps, (doc.id, doc.filename, steps)
        if doc.status in ("auto_approved", "held") and doc.doc_type in ("supplier_invoice", "customer_dispute", "remittance") and doc.duplicate_of is None:
            assert "classify" in steps and "match" in steps, (doc.id, steps)
