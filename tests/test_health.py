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


def test_stubs_return_501(client):
    for path in ("/status", "/queue", "/ledger", "/events", "/review/1"):
        assert client.get(path).status_code == 501, path
    for path in ("/approve/1", "/reject/1", "/retry/1", "/inject", "/reset"):
        assert client.post(path).status_code == 501, path


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Ops Agent" in r.text
