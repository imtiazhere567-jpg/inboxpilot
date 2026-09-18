"""SQLAlchemy engine/session helpers and a schema applier (schema.sql is the single source of truth)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from agent.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def apply_schema(engine: Engine | None = None) -> None:
    """Apply sql/schema.sql. Every statement is IF NOT EXISTS / ON CONFLICT so this is safe to re-run."""
    engine = engine or get_engine()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql))


def ping(engine: Engine | None = None) -> bool:
    engine = engine or get_engine()
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1


def reset_for_tests(engine: Engine | None = None) -> None:
    """Truncate every demo table (keeps customers/suppliers/rules). Used by tests and by pipeline.reset."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        # app_state references runs, so TRUNCATE ... CASCADE would wipe it too: keep its timestamps and restore the row.
        row = conn.execute(text("SELECT last_interaction_at, next_reset_at FROM app_state WHERE id = 1")).first()
        conn.execute(text("TRUNCATE review_notes, actions, decisions, documents, emails, runs, app_state RESTART IDENTITY CASCADE"))
        conn.execute(text("INSERT INTO app_state (id, last_interaction_at, next_reset_at, current_run_id) VALUES (1, :li, :nr, NULL)"),
                     {"li": row[0] if row else None, "nr": row[1] if row else None})
