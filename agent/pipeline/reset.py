"""Reset — wipe demo state and replay the 30 seed emails (the former n8n 04-reset flow).

    reset_demo(mode="inject")  -> feeds seed emails straight into the pipeline, one every `delay_s` seconds so
                                  the page shows them arriving (default; works with no Gmail account)
    reset_demo(mode="gmail")   -> sends the seed emails to the demo Gmail inbox; the IMAP poller ingests them

Idle-aware (Amendment A5): the scheduled reset only fires when nobody has touched the page for
RESET_IDLE_MINUTES; otherwise it defers 5 minutes and checks again. The page shows the countdown from /status.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from agent import events
from agent.config import get_settings
from agent.db import get_engine, reset_for_tests, session_scope
from agent.models import AppState, Run
from agent.pipeline.actions import delete_external_objects, execute_actions
from agent.pipeline.process import handle_inbound
from agent.rules import invalidate_cache, load_rules
from agent.seed_loader import load_all, read_seed_emails, to_inbound

log = logging.getLogger("ops_agent.reset")

_reset_lock = threading.Lock()


def is_resetting() -> bool:
    """True while state is being cleared (the short first phase)."""
    return bool(events.flags().get("resetting"))


def reset_in_progress() -> bool:
    """True from the start of a reset until the last seed email has been processed."""
    return _reset_lock.locked()


def reset_demo(mode: str = "inject", delay_s: float = 1.2, run_tag: str | None = None) -> dict:
    """Synchronous. Call start_reset_in_background() from request handlers."""
    if not _reset_lock.acquire(blocking=False):
        return {"started": False, "reason": "reset already running"}
    settings = get_settings()
    run_tag = run_tag or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    try:
        events.set_flag("resetting", True)
        events.set_flag("seeding", {"done": 0, "total": 0})
        with session_scope() as s:
            cleaned = delete_external_objects(s)
        reset_for_tests(get_engine())
        invalidate_cache()
        with session_scope() as s:
            load_all(s)  # master data stays; make sure it is present on a fresh DB
            rules = load_rules(s, force=True)
            run = Run(mode="shadow" if rules.shadow_mode else "live")
            s.add(run)
            s.flush()
            state = s.get(AppState, 1)
            state.current_run_id = run.id
            state.next_reset_at = datetime.now(timezone.utc) + timedelta(minutes=settings.reset_interval_minutes)
            run_id = run.id
        gmail_archived = 0
        if mode == "gmail":
            from agent.integrations.gmail import GmailClient

            g = GmailClient()
            if g.configured:
                gmail_archived = g.archive_all()
            else:
                mode = "inject"
        events.set_flag("resetting", False)
        events.bump("reset: state cleared")

        seeds = read_seed_emails()
        events.set_flag("seeding", {"done": 0, "total": len(seeds), "mode": mode})
        for i, seed in enumerate(seeds, 1):
            inbound = to_inbound(seed, run_tag)
            if mode == "gmail":
                from agent.integrations.gmail import GmailClient

                GmailClient().send_seed(inbound, run_tag)
            else:
                with session_scope() as s:
                    _, docs = handle_inbound(s, inbound)
                    for d in docs:
                        if d.status in ("auto_approved", "held"):
                            execute_actions(s, d)
            events.set_flag("seeding", {"done": i, "total": len(seeds), "mode": mode})
            if delay_s and i < len(seeds):
                time.sleep(delay_s)
        events.set_flag("seeding", None)
        events.bump("reset: seeding complete")
        try:
            from agent.integrations.slack import SlackClient

            sc = SlackClient()
            if sc.configured:
                sc.post(f"🔄 demo reset · run {run_id} · {len(seeds)} seed emails · mode {mode}")
        except Exception as exc:  # noqa: BLE001
            log.warning("reset slack notice failed: %s", exc)
        return {"started": True, "run_id": run_id, "mode": mode, "cleaned": cleaned, "gmail_archived": gmail_archived, "seeded": len(seeds)}
    except Exception as exc:  # noqa: BLE001
        log.exception("reset failed")
        events.set_flag("resetting", False)
        events.set_flag("seeding", None)
        events.set_flag("last_error", f"reset failed: {exc}")
        return {"started": True, "error": str(exc)}
    finally:
        _reset_lock.release()


def start_reset_in_background(mode: str = "inject", delay_s: float = 1.2) -> dict:
    if _reset_lock.locked():
        return {"started": False, "reason": "reset already running"}
    t = threading.Thread(target=reset_demo, kwargs={"mode": mode, "delay_s": delay_s}, name="ops-agent-reset", daemon=True)
    t.start()
    return {"started": True, "background": True}


def maybe_reset() -> str:
    """Scheduler job (every 5 min). Returns what it did, for logs."""
    settings = get_settings()
    if settings.app_mode == "live":
        return "live mode: no reset"
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        state = s.get(AppState, 1)
        if state is None or state.next_reset_at is None:
            return "no schedule"
        if now < state.next_reset_at:
            return "not due"
        idle_for = (now - state.last_interaction_at) if state.last_interaction_at else None
        if idle_for is not None and idle_for < timedelta(minutes=settings.reset_idle_minutes):
            state.next_reset_at = now + timedelta(minutes=5)
            return f"deferred: page active {int(idle_for.total_seconds())}s ago"
    mode = "gmail" if settings.integrations_configured["gmail"] else "inject"
    start_reset_in_background(mode=mode, delay_s=settings.seed_delay_seconds)
    return "reset started"


def seconds_to_reset() -> int | None:
    with session_scope() as s:
        state = s.get(AppState, 1)
        if state is None or state.next_reset_at is None:
            return None
        return max(0, int((state.next_reset_at - datetime.now(timezone.utc)).total_seconds()))
