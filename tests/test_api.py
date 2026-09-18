"""Phase 4/5/7 acceptance (offline): page API — inject, queue, review, approve/reject/retry, ledger, shadow, outage, login."""
from __future__ import annotations

import base64
import uuid

import pytest

from agent.config import get_settings
from agent.seed_loader import attachment_bytes, read_seed_emails


@pytest.fixture(scope="module", autouse=True)
def _fresh_state(db):
    """Each DB test module starts from an empty demo state (master data kept)."""
    from agent.db import reset_for_tests

    reset_for_tests(db)

SEEDS = {e["seed_no"]: e for e in read_seed_emails()}


def _inject(client, seed_no: int):
    s = SEEDS[seed_no]
    body = {"message_id": f"{s['message_id']}-api-{uuid.uuid4().hex[:6]}", "seed_no": seed_no, "from_addr": s["from_addr"],
            "subject": s["subject"], "body_text": s["body"],
            "attachments": [{"filename": fn, "content_base64": base64.b64encode(attachment_bytes(fn)).decode(), "mime": ""} for fn in s["attachments"]]}
    r = client.post("/inject", json=body, headers={"Authorization": f"Bearer {get_settings().reset_token}"})
    assert r.status_code == 200, r.text
    return r.json()["documents"]


@pytest.mark.usefixtures("db")
def test_inject_requires_token(client):
    r = client.post("/inject", json={"message_id": "x", "from_addr": "a@b.co.uk", "subject": "s", "body_text": "b"})
    assert r.status_code == 401


@pytest.mark.usefixtures("db")
def test_status_and_queue(client):
    _inject(client, 1)
    st = client.get("/status").json()
    assert st["mode"] in ("live", "shadow") and "counts" in st and "cost_usd" in st and st["llm"] == "fake"
    q = client.get("/queue").json()
    assert q["items"] and {"document_id", "status", "reason", "party_name", "cost_usd"} <= set(q["items"][0])


@pytest.mark.usefixtures("db")
def test_review_item_has_trail(client):
    doc = _inject(client, 4)[0]
    r = client.get(f"/review/{doc['id']}").json()
    assert r["item"]["status"] == "executed"
    assert any(t["step"] == "classify" for t in r["item"]["timeline"])
    assert any(d["step"] == "verify" for d in r["decisions"])
    assert r["item"]["source_snippet"] and "EGL-3310" in r["item"]["source_snippet"]
    assert client.get("/review/999999").status_code == 404


@pytest.mark.usefixtures("db")
def test_approve_held_executes(client):
    doc = _inject(client, 10)[0]
    assert doc["status"] == "held"
    r = client.post(f"/approve/{doc['id']}", json={"note": "checked with finance, PO raised"})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert {a["system"] for a in r.json()["actions"]} >= {"qbo"}
    assert client.post(f"/approve/{doc['id']}", json={"note": "again"}).status_code == 409
    led = client.get("/ledger?overrides=1").json()["rows"]
    assert any(row["document_id"] == doc["id"] and row["human_override"] for row in led)


@pytest.mark.usefixtures("db")
def test_reject_requires_note_and_blocks_approve(client):
    doc = _inject(client, 12)[0]
    assert client.post(f"/reject/{doc['id']}", json={"note": "no"}).status_code == 422  # min_length 3
    r = client.post(f"/reject/{doc['id']}", json={"note": "unknown supplier, forwarded to procurement"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert client.post(f"/approve/{doc['id']}", json={"note": "changed my mind"}).status_code == 409


@pytest.mark.usefixtures("db")
def test_outage_then_retry_from_api(client):
    assert client.post("/outage", json={"system": "hubspot"}).status_code == 200
    try:
        doc = _inject(client, 18)[0]
        assert doc["status"] == "failed"
        assert client.post(f"/retry/{doc['id']}").json()["status"] == "failed"  # still out
    finally:
        client.post("/outage", json={"system": "none"})
    r = client.post(f"/retry/{doc['id']}")
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert client.post(f"/retry/{doc['id']}").status_code == 409


@pytest.mark.usefixtures("db")
def test_shadow_toggle(client):
    assert client.post("/shadow", json={"enabled": True}).status_code == 200
    try:
        assert client.get("/status").json()["shadow_mode"] is True
        doc = _inject(client, 5)[0]
        assert doc["status"] == "shadow"
        r = client.post(f"/approve/{doc['id']}", json={"note": "approving from shadow"})
        assert r.json()["status"] == "executed"
    finally:
        client.post("/shadow", json={"enabled": False})
    assert client.get("/status").json()["shadow_mode"] is False


@pytest.mark.usefixtures("db")
def test_ledger_csv(client):
    r = client.get("/ledger?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("document_id,seed,received_at")


@pytest.mark.usefixtures("db")
def test_login_gate(client, settings):
    settings.demo_password = "letmein"
    try:
        assert client.get("/queue").status_code == 401
        assert client.get("/health").status_code == 200  # public
        assert client.get("/inside").status_code == 200  # public
        assert client.post("/login", json={"password": "nope"}).status_code == 401
        assert client.post("/login", json={"password": "letmein"}).status_code == 200
        assert client.get("/queue").status_code == 200
        client.post("/logout")
        assert client.get("/queue").status_code == 401
        # a bearer reset token still works for automation
        assert client.get("/status", headers={"Authorization": f"Bearer {settings.reset_token}"}).status_code == 200
    finally:
        settings.demo_password = ""


@pytest.mark.usefixtures("db")
def test_rate_limit(client, settings):
    old = settings.approve_rate_limit_per_minute
    settings.approve_rate_limit_per_minute = 2
    try:
        from agent.main import _hits

        _hits.clear()
        codes = [client.post("/outage", json={"system": "none"}).status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and 429 in codes[2:]
    finally:
        settings.approve_rate_limit_per_minute = old
        _hits.clear()


def test_index_and_inside_pages(client):
    assert "Ops Agent" in client.get("/").text
    assert "guardrail" in client.get("/inside").text.lower()
