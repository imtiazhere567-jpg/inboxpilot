"""Phase 5 acceptance (offline): reset wipes state, replays 30 seeds, is idle-aware, and never overlaps."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from agent.models import AppState, Document, Email, Run
from agent.pipeline.reset import maybe_reset, reset_demo, reset_in_progress, seconds_to_reset


@pytest.fixture(scope="module", autouse=True)
def _fresh_state(db):
    """Each DB test module starts from an empty demo state (master data kept)."""
    from agent.db import reset_for_tests

    reset_for_tests(db)


@pytest.mark.usefixtures("db")
def test_reset_replays_all_seeds(session):
    r = reset_demo(mode="inject", delay_s=0)
    assert r["started"] and r.get("run_id") and r["seeded"] == 31 and "error" not in r
    assert session.scalar(select(func.count(Email.id))) == 31
    assert session.scalar(select(func.count(Document.id))) == 33
    state = session.get(AppState, 1)
    assert state.current_run_id == r["run_id"] and state.next_reset_at is not None
    assert session.get(Run, r["run_id"]).mode == "live"
    statuses = {s for (s,) in session.execute(select(Document.status).distinct())}
    assert statuses >= {"executed", "held", "ignore"}
    assert seconds_to_reset() > 0
    session.commit()  # release the test transaction's table locks before TRUNCATE runs again
    # second reset: everything replaced, counts identical
    r2 = reset_demo(mode="inject", delay_s=0)
    assert r2["started"] and "error" not in r2 and r2["seeded"] == 31  # ids restart from 1 by design (stable doc numbers)
    session.expire_all()
    assert session.scalar(select(func.count(Email.id))) == 31


@pytest.mark.usefixtures("db")
def test_maybe_reset_is_idle_aware(session):
    now = datetime.now(timezone.utc)
    state = session.get(AppState, 1)
    state.next_reset_at = now + timedelta(hours=1)
    session.commit()
    assert maybe_reset() == "not due"

    state = session.get(AppState, 1)
    state.next_reset_at = now - timedelta(seconds=5)
    state.last_interaction_at = now - timedelta(minutes=1)  # someone is using the page
    session.commit()
    out = maybe_reset()
    assert out.startswith("deferred")
    session.expire_all()
    assert session.get(AppState, 1).next_reset_at > now

    state = session.get(AppState, 1)
    state.next_reset_at = now - timedelta(seconds=5)
    state.last_interaction_at = now - timedelta(hours=2)  # idle
    session.commit()
    session.close()  # no open transaction while the background thread truncates
    assert maybe_reset() == "reset started"
    for _ in range(600):  # background thread with SEED_DELAY_SECONDS=0 finishes in a few seconds
        time.sleep(0.1)
        if not reset_in_progress():
            break
    session.expire_all()
    assert session.scalar(select(func.count(Email.id))) == 31
