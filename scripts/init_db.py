"""Create the database named in DATABASE_URL (if missing) and apply sql/schema.sql. Safe to re-run.

Usage:  python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from agent.config import get_settings
from agent.db import apply_schema, get_engine, ping


def ensure_database() -> None:
    url = make_url(get_settings().database_url)
    target = url.database
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target}"'))
            print(f"created database {target}")
        else:
            print(f"database {target} already exists")
    admin.dispose()


if __name__ == "__main__":
    ensure_database()
    apply_schema(get_engine())
    print("schema applied; ping =", ping())
