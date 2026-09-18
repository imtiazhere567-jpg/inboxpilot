"""Load seed master data (suppliers, customers, rules) and read the seed email set.

Used by scripts/load_seed.py, by pipeline.reset (Phase 5) and by the tests. Idempotent: rows are upserted by name/key.
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.models import Customer, Rule, Supplier

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
ATTACHMENTS_DIR = SEED_DIR / "attachments"


def _patterns(cell: str) -> list[str]:
    return [p.strip() for p in cell.split("|") if p.strip()]


def load_suppliers(session: Session, path: Path = SEED_DIR / "suppliers.csv") -> int:
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            obj = session.scalar(select(Supplier).where(Supplier.name == row["name"])) or Supplier(name=row["name"])
            obj.identifier_patterns = _patterns(row["identifier_patterns"])
            obj.qbo_vendor_id = row.get("qbo_vendor_id") or obj.qbo_vendor_id or None
            obj.iban_last4 = row.get("iban_last4") or None
            obj.po_amount = Decimal(row["po_amount"]) if row.get("po_amount") else None
            session.add(obj)
            n += 1
    session.flush()
    return n


def load_customers(session: Session, path: Path = SEED_DIR / "customers.csv") -> int:
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            obj = session.scalar(select(Customer).where(Customer.name == row["name"])) or Customer(name=row["name"])
            obj.identifier_patterns = _patterns(row["identifier_patterns"])
            obj.hubspot_company_id = row.get("hubspot_company_id") or obj.hubspot_company_id or None
            obj.contact_email = row.get("contact_email") or None
            session.add(obj)
            n += 1
    session.flush()
    return n


def load_rules(session: Session, path: Path = SEED_DIR / "rules.csv", overwrite: bool = False) -> int:
    """Seed the rules table. With overwrite=False existing values are kept (the sheet is the live source)."""
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            obj = session.get(Rule, row["key"])
            if obj is None:
                obj = Rule(key=row["key"], value=row["value"], note=row.get("note"))
            elif overwrite:
                obj.value, obj.note = row["value"], row.get("note")
            session.add(obj)
            n += 1
    session.flush()
    return n


def prune_to_seed(session: Session) -> dict[str, int]:
    """Demo reset: remove parties added during the run (e.g. from a held item) so the seed story replays exactly."""
    import csv as _csv

    removed = {"suppliers": 0, "customers": 0}
    for model, path, key in ((Supplier, SEED_DIR / "suppliers.csv", "suppliers"), (Customer, SEED_DIR / "customers.csv", "customers")):
        with path.open(newline="", encoding="utf-8") as f:
            keep = {row["name"] for row in _csv.DictReader(f)}
        for obj in session.scalars(select(model)):
            if obj.name not in keep:
                session.delete(obj)
                removed[key] += 1
    session.flush()
    return removed


def load_all(session: Session, overwrite_rules: bool = False, prune: bool = False) -> dict[str, int]:
    out = {
        "suppliers": load_suppliers(session),
        "customers": load_customers(session),
        "rules": load_rules(session, overwrite=overwrite_rules),
    }
    if prune:
        out["pruned"] = sum(prune_to_seed(session).values())
    return out


def read_seed_emails(path: Path = SEED_DIR / "emails.json") -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["emails"]


def read_manifest(path: Path = ATTACHMENTS_DIR / "manifest.json") -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def attachment_bytes(filename: str) -> bytes:
    return (ATTACHMENTS_DIR / filename).read_bytes()


def to_inbound(seed: dict[str, Any], run_tag: str = "") -> "InboundEmail":
    """Turn a seed email dict into an InboundEmail. run_tag makes the message id unique per demo run."""
    from agent.pipeline.intake import InboundEmail

    from datetime import datetime, timedelta, timezone

    mid = seed["message_id"] + (f"-{run_tag}" if run_tag else "")
    # Spread arrivals over the last three days (10 per day, 08:00-17:30) so the dashboard's "today" and
    # "last 7 days" views look like a real inbox rather than 30 emails in one minute.
    n = seed["seed_no"]
    days_ago = max(0, (31 - n) // 11)
    slot = (n - 1) % 11
    base = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    received = base + timedelta(minutes=57 * slot + (n * 7) % 23)
    if received > datetime.now(timezone.utc):
        received = datetime.now(timezone.utc) - timedelta(minutes=(10 - slot) * 3)
    return InboundEmail(
        message_id=mid,
        from_addr=seed["from_addr"],
        subject=seed["subject"],
        body_text=seed["body"],
        attachments=[(fn, attachment_bytes(fn)) for fn in seed["attachments"]],
        seed_no=seed["seed_no"],
        received_at=received,
    )
