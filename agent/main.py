"""Ops Agent — FastAPI entry point.

  GET  /                        review queue page
  GET  /inside                  under-the-hood page (public)
  GET  /health                  liveness + which integrations are configured (public)
  GET  /status                  run mode, counts, cost, reset countdown, rules
  GET  /queue                   every document in the current run
  GET  /review/{id}             full review item + decision trail
  POST /approve/{id}            human approve (note required) -> actions
  POST /reject/{id}             human reject (note required)
  POST /retry/{id}              retry a failed action
  GET  /ledger[?format=csv&overrides=1&all=1]
  GET  /events                  SSE: "changed" whenever state moves
  POST /shadow {enabled}        toggle shadow mode (writes rules + sheet)
  POST /outage {system}         simulate an outage: none|qbo|hubspot|slack
  POST /reset                   wipe + reseed (bearer RESET_TOKEN, or logged-in demo session)
  POST /inject                  push one email into intake (bearer RESET_TOKEN)
  POST /login, /logout          shared demo password (only enforced when DEMO_PASSWORD is set)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent import __version__, events
from agent.config import get_settings
from agent.db import ping, session_scope
from agent.models import Document, ReviewNote, Rule
from agent.schemas import HealthResponse, InjectEmail, ReviewDecision
from agent.service import ledger, ledger_csv, queue, review_extras, review_item, status, touch_interaction

log = logging.getLogger("ops_agent")
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT.parent / "docs"

PUBLIC_PATHS = ("/health", "/login", "/logout", "/inside", "/static", "/docs", "/openapi.json", "/redoc", "/favicon.ico")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app.state.scheduler_running = False
    if settings.scheduler_enabled and settings.app_env != "test":
        from agent.scheduler import start_scheduler

        app.state.scheduler = start_scheduler()
        app.state.scheduler_running = True
    try:
        from agent.llm import export_prompts

        export_prompts(STATIC_DIR / "prompts.json")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not export prompts: %s", exc)
    log.info("ops-agent %s starting (env=%s)", __version__, settings.app_env)
    yield
    if getattr(app.state, "scheduler_running", False):
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Ops Agent — invoices & disputes", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- auth: one shared demo password, cookie carries an HMAC of it ---------------------------------------------

def _cookie_value() -> str:
    s = get_settings()
    return hmac.new((s.reset_token or "ops-agent").encode(), s.demo_password.encode(), hashlib.sha256).hexdigest()


def _is_logged_in(request: Request) -> bool:
    s = get_settings()
    if not s.demo_password:
        return True
    return hmac.compare_digest(request.cookies.get("ops_demo", ""), _cookie_value())


def _has_reset_token(request: Request) -> bool:
    s = get_settings()
    auth = request.headers.get("authorization", "")
    return bool(s.reset_token) and auth == f"Bearer {s.reset_token}"


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith(PUBLIC_PATHS) or _is_logged_in(request) or _has_reset_token(request):
        return await call_next(request)
    if path == "/" or request.headers.get("accept", "").startswith("text/html"):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "login required"}, status_code=401)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else request.client.host) if request.client else "?"


_hits: dict[str, deque[float]] = defaultdict(deque)


def rate_limited(request: Request) -> None:
    """Simple per-IP sliding window on human actions (Phase 7 hardening)."""
    limit = get_settings().approve_rate_limit_per_minute
    now = time.time()
    q = _hits[_client_ip(request)]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too many actions — wait a minute")
    q.append(now)


# --- pages ---------------------------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/inside", include_in_schema=False)
def inside() -> FileResponse:
    return FileResponse(DOCS_DIR / "inside.html")


@app.get("/login", include_in_schema=False)
def login_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "login.html").read_text(encoding="utf-8"))


class LoginBody(BaseModel):
    password: str


@app.post("/login", include_in_schema=False)
def login(body: LoginBody, request: Request, response: Response):
    s = get_settings()
    rate_limited(request)
    if not s.demo_password or hmac.compare_digest(body.password, s.demo_password):
        response.set_cookie("ops_demo", _cookie_value(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
                            secure=s.app_env == "prod")
        return {"ok": True}
    raise HTTPException(status_code=401, detail="wrong password")


@app.post("/logout", include_in_schema=False)
def logout(response: Response):
    response.delete_cookie("ops_demo")
    return {"ok": True}


# --- read ------------------------------------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    try:
        db_ok = ping()
    except Exception as exc:  # noqa: BLE001 — health must never raise
        log.warning("database ping failed: %s", exc)
        db_ok = False
    return HealthResponse(status="ok" if db_ok else "degraded", version=__version__, env=settings.app_env, database=db_ok,
                          integrations=settings.integrations_configured, scheduler=bool(getattr(app.state, "scheduler_running", False)))


@app.get("/status")
def get_status():
    with session_scope() as s:
        touch_interaction(s)
        return status(s)


@app.get("/queue")
def get_queue():
    with session_scope() as s:
        touch_interaction(s)
        return {"version": events.version(), "items": queue(s)}


@app.get("/review/{document_id}")
def get_review(document_id: int):
    with session_scope() as s:
        touch_interaction(s)
        item = review_item(s, document_id)
        if item is None:
            raise HTTPException(status_code=404, detail="no such document")
        return {"item": item.model_dump(mode="json"), **review_extras(s, document_id)}


@app.get("/ledger")
def get_ledger(format: Literal["json", "csv"] = "json", overrides: bool = False, all: bool = False):
    with session_scope() as s:
        touch_interaction(s)
        rows = ledger(s, overrides_only=overrides, all_runs=all)
        if format == "csv":
            return PlainTextResponse(ledger_csv(rows), media_type="text/csv",
                                     headers={"Content-Disposition": "attachment; filename=ops-agent-ledger.csv"})
        return {"rows": [r.model_dump(mode="json") for r in rows]}


@app.get("/events")
async def sse(request: Request):
    async def gen():
        last = events.version()
        yield {"event": "hello", "data": str(last)}
        beat = 0
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(1)
            v = events.version()
            if v != last:
                last = v
                yield {"event": "changed", "data": str(v)}
            beat += 1
            if beat % 15 == 0:
                yield {"event": "ping", "data": str(int(time.time()))}
    return EventSourceResponse(gen())


# --- human decisions -------------------------------------------------------------------------------------------

def _load_doc(s, document_id: int) -> Document:
    doc = s.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="no such document")
    return doc


@app.post("/approve/{document_id}")
def approve(document_id: int, body: ReviewDecision, request: Request, _: None = Depends(rate_limited)):
    from agent.pipeline.actions import execute_actions

    with session_scope() as s:
        touch_interaction(s)
        doc = _load_doc(s, document_id)
        if doc.status not in ("held", "shadow"):
            raise HTTPException(status_code=409, detail=f"cannot approve a document in status {doc.status}")
        doc.status = "approved"
        doc.reason = f"approved by human: {body.note}"
        s.add(ReviewNote(document_id=doc.id, action="approve", note=body.note, by=body.by))
        s.flush()
        execute_actions(s, doc)
        result = {"document_id": doc.id, "status": doc.status, "reason": doc.reason,
                  "actions": [{"system": a.system, "status": a.status, "external_id": a.external_id, "error": a.error} for a in doc.actions]}
    events.bump(f"approve #{document_id}")
    return result


@app.post("/reject/{document_id}")
def reject(document_id: int, body: ReviewDecision, request: Request, _: None = Depends(rate_limited)):
    with session_scope() as s:
        touch_interaction(s)
        doc = _load_doc(s, document_id)
        if doc.status not in ("held", "shadow", "failed"):
            raise HTTPException(status_code=409, detail=f"cannot reject a document in status {doc.status}")
        doc.status = "rejected"
        doc.reason = f"rejected by human: {body.note}"
        s.add(ReviewNote(document_id=doc.id, action="reject", note=body.note, by=body.by))
        result = {"document_id": doc.id, "status": doc.status, "reason": doc.reason}
    events.bump(f"reject #{document_id}")
    return result


@app.post("/retry/{document_id}")
def retry(document_id: int, request: Request, _: None = Depends(rate_limited)):
    from agent.pipeline.actions import execute_actions

    with session_scope() as s:
        touch_interaction(s)
        doc = _load_doc(s, document_id)
        if doc.status != "failed":
            raise HTTPException(status_code=409, detail=f"only failed documents can be retried (status {doc.status})")
        s.add(ReviewNote(document_id=doc.id, action="retry", note="retry requested from the page", by="demo"))
        execute_actions(s, doc)
        result = {"document_id": doc.id, "status": doc.status, "reason": doc.reason,
                  "actions": [{"system": a.system, "status": a.status, "external_id": a.external_id, "error": a.error} for a in doc.actions]}
    events.bump(f"retry #{document_id}")
    return result


# --- controls ---------------------------------------------------------------------------------------------------

class ShadowBody(BaseModel):
    enabled: bool


class OutageBody(BaseModel):
    system: Literal["none", "qbo", "hubspot", "slack"]


def _write_rule(key: str, value: str) -> None:
    from agent.rules import invalidate_cache

    with session_scope() as s:
        obj = s.get(Rule, key) or Rule(key=key, value=value)
        obj.value = value
        s.add(obj)
    if get_settings().integrations_configured["rules_sheet"]:
        try:
            from agent.integrations.sheets import write_rule

            write_rule(key, value)
        except Exception as exc:  # noqa: BLE001
            log.warning("sheet write %s failed: %s", key, exc)
    invalidate_cache()
    events.bump(f"rule {key}={value}")


@app.post("/shadow")
def set_shadow(body: ShadowBody, request: Request, _: None = Depends(rate_limited)):
    _write_rule("shadow_mode", "true" if body.enabled else "false")
    return {"shadow_mode": body.enabled}


@app.post("/outage")
def set_outage(body: OutageBody, request: Request, _: None = Depends(rate_limited)):
    _write_rule("simulate_outage", body.system)
    return {"simulate_outage": body.system}


@app.post("/reset")
def reset(request: Request, mode: Literal["inject", "gmail", "auto"] = "auto", _: None = Depends(rate_limited)):
    from agent.pipeline.reset import start_reset_in_background

    if not (_has_reset_token(request) or _is_logged_in(request)):
        raise HTTPException(status_code=401, detail="reset token or demo login required")
    if mode == "auto":
        mode = "gmail" if get_settings().integrations_configured["gmail"] else "inject"
    return start_reset_in_background(mode=mode, delay_s=get_settings().seed_delay_seconds)


@app.post("/inject")
def inject(body: InjectEmail, request: Request):
    import base64

    from agent.pipeline.actions import execute_actions
    from agent.pipeline.intake import InboundEmail
    from agent.pipeline.process import handle_inbound

    if not _has_reset_token(request):
        raise HTTPException(status_code=401, detail="bearer RESET_TOKEN required")
    inbound = InboundEmail(message_id=body.message_id, from_addr=body.from_addr, subject=body.subject, body_text=body.body_text,
                           attachments=[(a["filename"], base64.b64decode(a["content_base64"])) for a in body.attachments],
                           seed_no=body.seed_no)
    with session_scope() as s:
        email, docs = handle_inbound(s, inbound)
        for d in docs:
            if d.status in ("auto_approved", "held"):
                execute_actions(s, d)
        result = {"email_id": email.id, "documents": [{"id": d.id, "filename": d.filename, "status": d.status, "reason": d.reason} for d in docs]}
    events.bump(f"inject {body.message_id}")
    return result
