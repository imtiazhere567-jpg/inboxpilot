"""APScheduler jobs — replaces the n8n flows (Amendment A1).

  intake   every INTAKE_POLL_SECONDS   pipeline.poll.poll_inbox      (only when Gmail is configured)
  reset    every 5 minutes             pipeline.reset.maybe_reset    (demo mode only; idle-aware)
  seed     once at startup             pipeline.reset.reset_demo     (demo mode, only if the database has no emails)

ensure_jobs() is re-run after settings are saved from the dashboard so the Gmail poller starts/stops without a restart.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from agent.config import get_settings
from agent.db import session_scope
from agent.models import Email

log = logging.getLogger("ops_agent.scheduler")


def ensure_jobs(scheduler: BackgroundScheduler) -> None:
    settings = get_settings()
    has_intake = scheduler.get_job("intake") is not None
    if settings.integrations_configured["gmail"] and not has_intake:
        from agent.pipeline.poll import poll_inbox

        scheduler.add_job(poll_inbox, "interval", seconds=settings.intake_poll_seconds, id="intake", max_instances=1, coalesce=True)
        log.info("gmail poller started (%s every %ss)", settings.gmail_address, settings.intake_poll_seconds)
    elif not settings.integrations_configured["gmail"] and has_intake:
        scheduler.remove_job("intake")
        log.info("gmail poller stopped")
    if scheduler.get_job("reset") is None:
        from agent.pipeline.reset import maybe_reset

        scheduler.add_job(maybe_reset, "interval", minutes=5, id="reset", max_instances=1, coalesce=True)


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    ensure_jobs(scheduler)

    if settings.auto_seed_on_start and settings.app_mode == "demo":
        from agent.pipeline.reset import start_reset_in_background

        try:
            with session_scope() as s:
                n = s.scalar(select(func.count(Email.id))) or 0
            if n == 0:
                mode = "gmail" if settings.integrations_configured["gmail"] else "inject"
                log.info("no emails in database — seeding demo (%s)", mode)
                start_reset_in_background(mode=mode, delay_s=settings.seed_delay_seconds)
        except Exception as exc:  # noqa: BLE001 — database may be down; /health will say so
            log.warning("auto-seed skipped: %s", exc)

    scheduler.start()
    log.info("scheduler started (mode=%s, gmail poll=%s, reset every %smin, idle guard %smin)", settings.app_mode,
             "on" if settings.integrations_configured["gmail"] else "off", settings.reset_interval_minutes, settings.reset_idle_minutes)
    return scheduler
