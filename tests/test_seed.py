"""Phase 1 acceptance: 30 seed emails, every document has an expected outcome, attachments regenerate byte-identically."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfReader

from agent.seed_loader import ATTACHMENTS_DIR, SEED_DIR, attachment_bytes, read_manifest, read_seed_emails

VALID_STATUS = {"auto_approved", "held", "ignore"}
VALID_DOC_TYPES = {"supplier_invoice", "customer_dispute", "credit_note", "remittance", "ignore", "unknown"}
VALID_ACTIONS = {"qbo", "hubspot", "slack"}


@pytest.fixture(scope="module")
def emails():
    return read_seed_emails()


@pytest.fixture(scope="module")
def manifest():
    return read_manifest()


def test_thirty_emails_in_order(emails):
    assert len(emails) == 30
    assert [e["seed_no"] for e in emails] == list(range(1, 31))
    assert len({e["message_id"] for e in emails}) == 30


def test_every_email_has_expected_outcome(emails):
    for e in emails:
        docs = e["expected"]["documents"]
        assert docs, f"seed {e['seed_no']} has no expected documents"
        for d in docs:
            assert d["status"] in VALID_STATUS, (e["seed_no"], d)
            assert d["doc_type"] in VALID_DOC_TYPES, (e["seed_no"], d)
            assert d["party_kind"] in {"supplier", "customer", None}, (e["seed_no"], d)
            if d["status"] == "held":
                assert d["reason_contains"], f"seed {e['seed_no']}: held documents must say why"
        assert set(e["expected"]["actions"]) <= VALID_ACTIONS, e["seed_no"]


def test_scenario_coverage(emails):
    """The seed set must exercise every path in PLAN.md §5."""
    docs = [d for e in emails for d in e["expected"]["documents"]]
    statuses = Counter(d["status"] for d in docs)
    assert statuses["auto_approved"] >= 15 and statuses["held"] >= 9 and statuses["ignore"] >= 3
    reasons = " | ".join((d["reason_contains"] or "") for d in docs)
    for needle in ("threshold", "ambiguous", "no supplier match", "duplicate", "invoice number", "could not read",
                   "refund", "name only", "legal", "sender", "instruction"):
        assert needle in reasons, f"missing scenario: {needle}"
    assert any(d["doc_type"] == "remittance" for d in docs)
    assert any(len(e["expected"]["documents"]) == 2 for e in emails), "need an email yielding two documents"
    assert any(a.endswith(".zip") for e in emails for a in e["attachments"])
    assert any(a.endswith(".csv") for e in emails for a in e["attachments"])


def test_attachments_exist_and_match_manifest(emails, manifest):
    for e in emails:
        for fn in e["attachments"]:
            assert fn in manifest, f"seed {e['seed_no']}: {fn} missing from manifest"
            assert hashlib.sha256(attachment_bytes(fn)).hexdigest() == manifest[fn]["sha256"], fn
        for d in e["expected"]["documents"]:
            if "sha256" in d:
                assert d["sha256"] == manifest[d["filename"]]["sha256"]


def test_duplicate_scenario_shares_bytes(emails, manifest):
    e3 = next(e for e in emails if e["seed_no"] == 3)
    e13 = next(e for e in emails if e["seed_no"] == 13)
    assert e3["attachments"] == e13["attachments"]
    d13 = e13["expected"]["documents"][0]
    assert d13["status"] == "ignore" and d13["duplicate_of_seed"] == 3


def test_reissue_scenario_differs_in_bytes_not_number(emails, manifest):
    assert manifest["EGL-3310.pdf"]["sha256"] != manifest["EGL-3310-reissued.pdf"]["sha256"]
    e4 = next(e for e in emails if e["seed_no"] == 4)["expected"]["documents"][0]
    e14 = next(e for e in emails if e["seed_no"] == 14)["expected"]["documents"][0]
    assert e4["extracted"]["invoice_number"] == e14["extracted"]["invoice_number"] == "EGL-3310"


def test_pdfs_are_text_extractable_except_corrupt(manifest):
    for name in manifest:
        if not name.endswith(".pdf"):
            continue
        if name == "HPC-8811.pdf":
            with pytest.raises(Exception):
                PdfReader(str(ATTACHMENTS_DIR / name)).pages[0].extract_text()
            continue
        text = "\n".join(p.extract_text() or "" for p in PdfReader(str(ATTACHMENTS_DIR / name)).pages)
        assert len(text) > 300, name


def test_invoice_pdfs_contain_number_and_total(emails):
    for e in emails:
        for d in e["expected"]["documents"]:
            if d["doc_type"] != "supplier_invoice" or "extracted" not in d:
                continue
            fn = d["filename"]
            raw = attachment_bytes(fn)
            text = raw.decode("utf-8") if fn.endswith(".csv") else "\n".join(p.extract_text() or "" for p in PdfReader(str(ATTACHMENTS_DIR / fn)).pages)
            assert d["extracted"]["invoice_number"] in text, fn
            total = d["extracted"]["total"]
            assert total in text or f"{float(total):,.2f}" in text, (fn, total)


def test_zip_members_listed(manifest):
    with zipfile.ZipFile(ATTACHMENTS_DIR / "BLU-0463.zip") as z:
        names = sorted(z.namelist())
    assert names == sorted(manifest["BLU-0463.zip"]["members"])
    for n in names:
        with zipfile.ZipFile(ATTACHMENTS_DIR / "BLU-0463.zip") as z:
            assert hashlib.sha256(z.read(n)).hexdigest() == manifest[n]["sha256"]


def test_regeneration_is_byte_identical(tmp_path, manifest):
    subprocess.run([sys.executable, str(SEED_DIR / "make_attachments.py"), "--out", str(tmp_path)],
                   check=True, capture_output=True)
    regenerated = json.loads((tmp_path / "manifest.json").read_text())
    assert {k: v["sha256"] for k, v in regenerated.items()} == {k: v["sha256"] for k, v in manifest.items()}


def test_nothing_real(emails):
    """Non-negotiable #10: no real domains or people. Every address must be under the .example TLD."""
    for e in emails:
        assert e["from_addr"].endswith(".example"), e["from_addr"]
