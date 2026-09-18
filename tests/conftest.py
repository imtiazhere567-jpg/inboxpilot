"""Test fixtures. Tests never start the scheduler and never call a real external system unless RUN_REAL_LLM=1."""
from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SEED_DELAY_SECONDS", "0")
os.environ.setdefault("RESET_TOKEN", "test-token")
os.environ["DEMO_PASSWORD"] = ""
if os.environ.get("RUN_REAL_LLM") != "1":
    os.environ["LLM_FAKE"] = "true"

import pytest
from fastapi.testclient import TestClient

from agent.config import get_settings


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def client(settings):
    from agent import cache
    from agent.main import app

    cache.clear()  # tests change the database directly, which the read cache cannot see
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def db(settings):
    """Schema applied, demo tables empty, master data loaded. Skips cleanly if Postgres is not reachable."""
    from agent.db import apply_schema, get_engine, ping, reset_for_tests, session_scope
    from agent.rules import invalidate_cache
    from agent.seed_loader import load_all

    try:
        engine = get_engine()
        ping(engine)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database not reachable: {exc}")
    apply_schema(engine)
    reset_for_tests(engine)
    with session_scope() as s:
        load_all(s, overwrite_rules=True)
    invalidate_cache()
    return engine


@pytest.fixture()
def session(db):
    from agent.db import get_sessionmaker

    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    finally:
        s.close()
