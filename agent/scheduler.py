"""APScheduler jobs — replaces the n8n flows (Amendment A1).

  intake   every INTAKE_POLL_SECONDS   pipeline.poll.poll_inbox      (only when Gmail is configured)
  reset    every 5 minutes             pipeline.reset.maybe_reset    (idle-aware; fires when the interval is due)
  seed     once at startup             pipeline.reset.reset_demo     (only if the database has no emails yet)
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from agent.config import get_settings
from agent.db import session_scope
from agent.models import Email

log = logging.getLogger("ops_agent.scheduler")


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")

    if settings.integrations_configured["gmail"]:
        from agent.pipeline.poll import poll_inbox

        scheduler.add_job(poll_inbox, "interval", seconds=settings.intake_poll_seconds, id="intake",
                          max_instances=1, coalesce=True)

    from agent.pipeline.reset import maybe_reset, start_reset_in_background

    scheduler.add_job(maybe_reset, "interval", minutes=5, id="reset", max_instances=1, coalesce=True)

    if settings.auto_seed_on_start:
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
    log.info("scheduler started (gmail poll=%s, reset every %smin, idle guard %smin)",
             "on" if settings.integrations_configured["gmail"] else "off",
             settings.reset_interval_minutes, settings.reset_idle_minutes)
    return scheduler
