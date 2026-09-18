"""Phase 0 acceptance: GET /health returns ok (or degraded with a clear reason) and never raises."""


def test_health_shape(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["env"] == "test"
    assert body["scheduler"] is False
    assert set(body["integrations"]) == {"anthropic", "gmail", "rules_sheet", "hubspot", "qbo", "slack"}
    assert isinstance(body["database"], bool)


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Ops Agent" in r.text
