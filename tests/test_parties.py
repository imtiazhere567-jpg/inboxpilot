"""Directory: list / add / edit / delete, suggestion from a held item, add-from-document reprocess, reset prune."""
from __future__ import annotations

import pytest

from agent.pipeline.reset import reset_demo


@pytest.fixture(scope="module", autouse=True)
def _seeded(db):
    from agent.db import reset_for_tests

    reset_for_tests(db)
    reset_demo(mode="inject", delay_s=0)


def _held_doc(client, seed_no):
    return next(i for i in client.get("/queue").json()["items"] if i["seed_no"] == seed_no)


def test_list_and_counts(client):
    sup = client.get("/api/parties?kind=supplier").json()["items"]
    cus = client.get("/api/parties?kind=customer").json()["items"]
    assert len(sup) == 10 and len(cus) == 12
    acme = next(p for p in sup if p["name"] == "Acme Supplies Ltd")
    assert acme["documents"] == 3 and acme["held"] == 1 and acme["po_amount"] == 1240.0


def test_add_edit_delete_and_validation(client):
    r = client.post("/api/parties/customer", json={"name": "Harbourside Hotel", "identifier_patterns": "harbourside-hotel.example, HSH-", "contact_email": "gm@harbourside-hotel.example"})
    assert r.status_code == 200
    pid = r.json()["id"]
    row = next(p for p in r.json()["items"] if p["id"] == pid)
    assert row["identifier_patterns"] == ["harbourside-hotel.example", "HSH-"]
    assert client.put(f"/api/parties/customer/{pid}", json={"name": "Harbourside Hotel Ltd", "identifier_patterns": ["harbourside-hotel.example"]}).status_code == 200
    assert client.post("/api/parties/customer", json={"name": "harbourside hotel ltd", "identifier_patterns": "x.example"}).status_code == 422  # duplicate
    assert client.post("/api/parties/customer", json={"name": "No Ids", "identifier_patterns": ""}).status_code == 422
    assert client.delete(f"/api/parties/customer/{pid}").status_code == 200
    assert client.delete(f"/api/parties/customer/{pid}").status_code == 404


def test_suggest_and_add_from_document_reprocesses(client):
    doc = _held_doc(client, 12)
    assert doc["status"] == "held" and doc["reason"].startswith("no supplier match")
    sg = client.get(f"/api/parties/suggest/{doc['document_id']}").json()
    assert sg["kind"] == "supplier" and "northstar-hygiene.example" in sg["identifier_patterns"] and "NSH-" in sg["identifier_patterns"]
    r = client.post(f"/api/parties/from_document/{doc['document_id']}?kind=supplier", json={"name": sg["name"], "identifier_patterns": sg["identifier_patterns"], "po_amount": "430.00"})
    assert r.status_code == 200 and r.json()["status"] == "executed", r.text
    rv = client.get(f"/review/{doc['document_id']}").json()
    assert rv["item"]["party_name"] == "Northstar Hygiene Ltd"
    assert any(d["step"] == "reprocess" for d in rv["decisions"]), "audit trail keeps the reprocess marker"
    assert client.post(f"/api/parties/from_document/{doc['document_id']}?kind=supplier", json={"name": "X", "identifier_patterns": "x.example"}).status_code == 409


def test_import_needs_connection(client):
    assert client.post("/api/parties/import/qbo").status_code == 409
    assert client.post("/api/parties/import/hubspot").status_code == 409


def test_reset_prunes_added_parties(client):
    assert any(p["name"] == "Northstar Hygiene Ltd" for p in client.get("/api/parties?kind=supplier").json()["items"])
    reset_demo(mode="inject", delay_s=0)
    names = [p["name"] for p in client.get("/api/parties?kind=supplier").json()["items"]]
    assert "Northstar Hygiene Ltd" not in names and len(names) == 10
    assert _held_doc(client, 12)["status"] == "held"


def test_directory_page(client):
    assert "Suppliers" in client.get("/directory").text
