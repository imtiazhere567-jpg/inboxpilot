"""Slack — one line per outcome in #ops-agent. Reference numbers only, never customer email/phone (Amendment A8)."""
from __future__ import annotations

import logging

from agent.config import get_settings
from agent.integrations.base import IntegrationError

log = logging.getLogger("ops_agent.slack")


class SlackClient:
    def __init__(self) -> None:
        s = get_settings()
        self.configured = bool(s.slack_bot_token)
        self.channel = s.slack_channel
        self._client = None
        if self.configured:
            from slack_sdk import WebClient

            self._client = WebClient(token=s.slack_bot_token)

    def post(self, text: str) -> str:
        """Returns "<channel_id>:<ts>" so the message can be deleted on reset."""
        try:
            resp = self._client.chat_postMessage(channel=self.channel, text=text, unfurl_links=False)
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"slack: {exc}") from exc
        return f"{resp['channel']}:{resp['ts']}"

    def delete(self, external_id: str) -> None:
        if not self.configured or not external_id or ":" not in external_id:
            return
        channel, ts = external_id.split(":", 1)
        try:
            self._client.chat_delete(channel=channel, ts=ts)
        except Exception as exc:  # noqa: BLE001 — cleanup is best effort
            log.warning("slack delete %s failed: %s", external_id, exc)
