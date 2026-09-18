"""Ops Agent — FastAPI entry point.

Routes (Phase 0 ships /health only; the rest are stubs that 501 until their phase lands):
  GET  /health                  liveness + which integrations are configured
  GET  /status                  run mode, next reset countdown, cost this run        (Phase 4)
  GET  /queue                   documents by status for the page                     (Phase 4)
  GET  /review/{document_id}    full review item                                     (Phase 4)
  POST /approve/{document_id}   human approve  -> actions                            (Phase 4)
  POST /reject/{document_id}    human reject (note required)                         (Phase 4)
  POST /retry/{document_id}     retry a failed action                                (Phase 4)
  GET  /ledger                  ledger rows (+ ?format=csv)                          (Phase 4)
  GET  /events                  SSE stream of document state changes                 (Phase 4)
  POST /inject                  push a seed email straight into intake (token)       (Phase 3)
  POST /reset                   wipe demo state and reseed (token)                   (Phase 5)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent import __version__
from agent.config import get_settings
from agent.db import ping
from agent.schemas import HealthResponse

log = logging.getLogger("ops_agent")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app.state.scheduler_running = False
    if settings.scheduler_enabled and settings.app_env != "test":
        from agent.scheduler import start_scheduler  # imported lazily so tests never start it

        app.state.scheduler = start_scheduler()
        app.state.scheduler_running = True
    log.info("ops-agent %s starting (env=%s)", __version__, settings.app_env)
    yield
    if getattr(app.state, "scheduler_running", False):
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Ops Agent — invoices & disputes", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    try:
        db_ok = ping()
    except Exception as exc:  # noqa: BLE001 — health must never raise
        log.warning("database ping failed: %s", exc)
        db_ok = False
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        env=settings.app_env,
        database=db_ok,
        integrations=settings.integrations_configured,
        scheduler=bool(getattr(app.state, "scheduler_running", False)),
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --- stubs: each returns 501 until its phase is built -------------------------------------------------

def _not_built(phase: int) -> HTTPException:
    return HTTPException(status_code=501, detail=f"Not built yet — arrives in Phase {phase}")


@app.get("/status")
def status():
    raise _not_built(4)


@app.get("/queue")
def queue():
    raise _not_built(4)


@app.get("/review/{document_id}")
def review(document_id: int):
    raise _not_built(4)


@app.post("/approve/{document_id}")
def approve(document_id: int):
    raise _not_built(4)


@app.post("/reject/{document_id}")
def reject(document_id: int):
    raise _not_built(4)


@app.post("/retry/{document_id}")
def retry(document_id: int):
    raise _not_built(4)


@app.get("/ledger")
def ledger():
    raise _not_built(4)


@app.get("/events")
def events():
    raise _not_built(4)


@app.post("/inject")
def inject():
    raise _not_built(3)


@app.post("/reset")
def reset():
    raise _not_built(5)
