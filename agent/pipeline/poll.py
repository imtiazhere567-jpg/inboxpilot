"""Gmail poller — scheduler job. Fetch unseen mail, run intake + process + actions, move to the processed label."""
from __future__ import annotations

import logging

from agent import events
from agent.db import session_scope
from agent.integrations.base import IntegrationError
from agent.integrations.gmail import GmailClient
from agent.pipeline.actions import execute_actions
from agent.pipeline.process import handle_inbound

log = logging.getLogger("ops_agent.poll")


def poll_inbox() -> int:
    g = GmailClient()
    if not g.configured:
        return 0
    try:
        items = g.fetch_unseen()
    except IntegrationError as exc:
        log.warning("poll: %s", exc)
        events.set_flag("last_error", str(exc))
        return 0
    done: list[str] = []
    for uid, inbound in items:
        try:
            with session_scope() as s:
                _, docs = handle_inbound(s, inbound)
                for d in docs:
                    if d.status in ("auto_approved", "held"):
                        execute_actions(s, d)
            done.append(uid)
            events.bump(f"poll: {inbound.message_id}")
        except Exception as exc:  # noqa: BLE001 — one bad message must not stop the poller
            log.exception("poll: failed on %s", inbound.message_id)
            events.set_flag("last_error", f"poll {inbound.message_id}: {exc}")
    g.mark_processed(done)
    return len(done)
