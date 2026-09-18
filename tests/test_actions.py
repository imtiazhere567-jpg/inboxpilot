"""Phase 3 acceptance (offline): actions run idempotently, simulate when unconfigured, fail + retry on outage."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from agent.llm import get_llm
from agent.models import Action, Rule
from agent.pipeline.actions import execute_actions
from agent.pipeline.process import handle_inbound
from agent.rules import RuleSet, invalidate_cache, load_rules
from agent.seed_loader import read_seed_emails, to_inbound


@pytest.fixture(scope="module", autouse=True)
def _fresh_state(db):
    """Each DB test module starts from an empty demo state (master data kept)."""
    from agent.db import reset_for_tests

    reset_for_tests(db)

SEEDS = {e["seed_no"]: e for e in read_seed_emails()}


def _run(session, seed_no: int):
    tag = uuid.uuid4().hex[:8]
    _, docs = handle_inbound(session, to_inbound(SEEDS[seed_no], tag), get_llm())
    session.commit()
    return docs


def _set_rule(session, key, value):
    obj = session.get(Rule, key) or Rule(key=key, value=value)
    obj.value = value
    session.add(obj)
    session.commit()
    invalidate_cache()


@pytest.mark.usefixtures("db")
def test_invoice_executes_simulated_and_is_idempotent(session):
    doc = _run(session, 1)[0]
    assert doc.status == "auto_approved"
    execute_actions(session, doc)
    session.commit()
    assert doc.status == "executed"
    systems = sorted(a.system for a in doc.actions)
    assert systems == ["qbo", "slack"]
    assert all(a.status == "ok" and a.external_id and a.result["simulated"] for a in doc.actions)
    n = len(doc.actions)
    execute_actions(session, doc)  # replay
    session.commit()
    session.refresh(doc)
    assert len(doc.actions) == n and doc.status == "executed"


@pytest.mark.usefixtures("db")
def test_dispute_creates_ticket_and_remittance_only_slack(session):
    d = _run(session, 19)[0]
    execute_actions(session, d)
    assert sorted(a.system for a in d.actions) == ["hubspot", "slack"]
    r = _run(session, 30)[0]
    execute_actions(session, r)
    assert [a.system for a in r.actions] == ["slack"] and r.status == "executed"


@pytest.mark.usefixtures("db")
def test_held_only_notifies(session):
    doc = _run(session, 9)[0]
    assert doc.status == "held"
    execute_actions(session, doc)
    assert doc.status == "held" and [a.system for a in doc.actions] == ["slack"]


@pytest.mark.usefixtures("db")
def test_outage_fails_then_retry_succeeds(session):
    _set_rule(session, "simulate_outage", "qbo")
    try:
        doc = _run(session, 2)[0]
        execute_actions(session, doc, load_rules(session, force=True))
        session.commit()
        assert doc.status == "failed" and "simulated outage" in doc.reason
        failed = [a for a in doc.actions if a.system == "qbo"]
        assert failed and failed[0].status == "failed"
        assert not [a for a in doc.actions if a.system == "slack"], "slack must not fire when the bill failed"
    finally:
        _set_rule(session, "simulate_outage", "none")
    execute_actions(session, doc, load_rules(session, force=True))
    session.commit()
    session.refresh(doc)
    assert doc.status == "executed"
    qbo = [a for a in doc.actions if a.system == "qbo"]
    assert sorted(a.status for a in qbo) == ["failed", "ok"], "retry adds one ok row, keeps the failed row for the audit trail"
    assert "failed:" not in (doc.reason or "")


@pytest.mark.usefixtures("db")
def test_shadow_mode_executes_nothing(session):
    _set_rule(session, "shadow_mode", "true")
    try:
        doc = _run(session, 3)[0]
        assert doc.status == "shadow" and "would be auto_approved" in doc.reason
        execute_actions(session, doc, load_rules(session, force=True))
        assert doc.actions == [] and doc.status == "shadow"
    finally:
        _set_rule(session, "shadow_mode", "false")
