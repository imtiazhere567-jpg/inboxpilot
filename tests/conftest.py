"""Test fixtures. Tests never start the scheduler and never call a real external system."""
from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("LLM_FAKE", "true")

import pytest
from fastapi.testclient import TestClient

from agent.config import get_settings


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def client(settings):
    from agent.main import app

    with TestClient(app) as c:
        yield c
