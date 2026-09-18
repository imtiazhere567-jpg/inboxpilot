"""APScheduler jobs — replaces the n8n flows (Amendment A1).

Jobs are registered here but their bodies live in agent/pipeline/*. Phase 0 registers nothing that
touches an external system; each phase enables its job when the pipeline module lands.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from agent.config import get_settings

log = logging.getLogger("ops_agent.scheduler")


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")

    # Phase 3: intake poll
    #   from agent.pipeline.intake import poll_inbox
    #   scheduler.add_job(poll_inbox, "interval", seconds=settings.intake_poll_seconds, id="intake", max_instances=1)
    # Phase 5: idle-aware reset
    #   from agent.pipeline.reset import maybe_reset
    #   scheduler.add_job(maybe_reset, "interval", minutes=5, id="reset", max_instances=1)

    scheduler.start()
    log.info("scheduler started (poll=%ss, reset every %smin, idle guard %smin)",
             settings.intake_poll_seconds, settings.reset_interval_minutes, settings.reset_idle_minutes)
    return scheduler
