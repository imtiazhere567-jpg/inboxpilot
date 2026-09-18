"""Dashboard, Ask-the-agent and Settings (offline)."""
from __future__ import annotations

import pytest

from agent.pipeline.reset import reset_demo


@pytest.fixture(scope="module", autouse=True)
def _seeded(db):
    from agent.db import reset_for_tests

    reset_for_tests(db)
    reset_demo(mode="inject", delay_s=0)


def test_dashboard_shape(client):
    d = client.get("/dashboard?tz=0").json()
    t = d["today"]
    assert {"emails", "invoices", "invoices_total", "disputes", "held", "executed", "cost_usd", "manual_minutes"} <= set(t)
    assert len(d["days"]) == 7 and sum(x["received"] for x in d["days"]) == 32
    assert d["attention_total"] == 10 and all(a["status"] in ("held", "failed") for a in d["attention"])
    assert d["feed"] and d["top_suppliers"][0]["name"] == "Acme Supplies Ltd"
    assert d["run"]["documents"] == 32


def test_ask_scope_and_document_lookup(client):
    r = client.post("/ask", json={"question": "what is the capital of France?"}).json()
    assert r["answer"].startswith("I can only answer questions about this inbox")
    r = client.post("/ask", json={"question": "show me ACME-2099"}).json()
    assert "doc #" in r["answer"] and "held" in r["answer"] and "1,203.90" in r["answer"]
    r = client.post("/ask", json={"question": "open doc #9"}).json()
    assert "IBL-7710" in r["answer"]
    r = client.post("/ask", json={"question": "show me ZZZ-9999"}).json()
    assert "can't find" in r["answer"]


def test_ask_offline_answers(client):
    r = client.post("/ask", json={"question": "what is held and why?"}).json()
    assert "10 item(s)" in r["answer"] and "IBL-7710" in r["answer"] and r["model"] == "fake"
    r = client.post("/ask", json={"question": "total from acme"}).json()
    assert "Acme Supplies Ltd" in r["answer"] and "£" in r["answer"]
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_settings_roundtrip(client):
    v = client.get("/api/settings").json()
    assert v["mode"] == "demo" and "gmail" in v["groups"]
    r = client.post("/api/settings", json={"values": {"slack_channel": "#ops-test", "slack_bot_token": "xoxb-not-real"}}).json()
    assert set(r["changed"]) == {"slack_channel", "slack_bot_token"}
    row = {x["key"]: x for x in r["groups"]["slack"]}
    assert row["slack_channel"]["value"] == "#ops-test" and row["slack_bot_token"]["value"] == "••••••••" and row["slack_bot_token"]["source"] == "settings"
    assert client.get("/status").json()["integrations"]["slack"] is True
    # echoing the mask back must not overwrite the secret; blank removes it
    r = client.post("/api/settings", json={"values": {"slack_bot_token": "••••••••"}}).json()
    assert r["changed"] == []
    r = client.post("/api/settings", json={"values": {"slack_bot_token": "", "slack_channel": ""}}).json()
    assert client.get("/status").json()["integrations"]["slack"] is False
    t = client.post("/api/settings/test/slack").json()
    assert t["ok"] is False and "token" in t["message"]


def test_live_mode_blocks_reset(client):
    assert client.post("/api/settings", json={"values": {"app_mode": "live"}}).status_code == 200
    try:
        assert client.get("/status").json()["app_mode"] == "live"
        assert client.post("/reset").status_code == 409
        from agent.pipeline.reset import maybe_reset

        assert maybe_reset() == "live mode: no reset"
    finally:
        client.post("/api/settings", json={"values": {"app_mode": "demo"}})
    assert client.get("/status").json()["app_mode"] == "demo"


def test_pages_serve(client):
    for path, needle in (("/", "Where the documents went"), ("/inbox", "Human decisions"), ("/ask", "Ask the agent"), ("/settings", "Gmail inbox"), ("/inside", "guardrails")):
        r = client.get(path)
        assert r.status_code == 200 and needle.lower() in r.text.lower(), path


def test_documents_redirects_to_inbox(client):
    r = client.get("/documents", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/inbox"
