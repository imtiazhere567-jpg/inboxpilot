"""Gmail demo account via IMAP (read) and SMTP (send seed mail on reset). App password, no OAuth (Amendment A3).

Seed emails are sent *from the demo account to itself*. Gmail rewrites the From header on SMTP submission, so the
intended sender, seed number and run tag travel in custom headers, which Gmail preserves:
    X-Ops-Agent-From, X-Ops-Agent-Seed, X-Ops-Agent-Run
The poller reads those headers first and falls back to the real From for any other mail that reaches the inbox.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from agent.config import get_settings
from agent.integrations.base import IntegrationError
from agent.pipeline.intake import InboundEmail

log = logging.getLogger("ops_agent.gmail")


class GmailClient:
    def __init__(self) -> None:
        s = get_settings()
        self.configured = bool(s.gmail_address and s.gmail_app_password)
        self.s = s

    # --- read -----------------------------------------------------------------------------------------------
    def fetch_unseen(self, limit: int = 50) -> list[tuple[str, InboundEmail]]:
        """Returns [(imap_uid, InboundEmail)] for unseen messages in the inbox folder. Marks them seen."""
        from imap_tools import AND, MailBox

        out = []
        try:
            with MailBox(self.s.gmail_imap_host).login(self.s.gmail_address, self.s.gmail_app_password,
                                                       initial_folder=self.s.gmail_label_inbox) as mb:
                for msg in mb.fetch(AND(seen=False), mark_seen=True, limit=limit, bulk=True):
                    headers = {k.lower(): v for k, v in msg.headers.items()}
                    from_addr = (headers.get("x-ops-agent-from", ("",))[0] or msg.from_).strip()
                    seed = headers.get("x-ops-agent-seed", ("",))[0].strip()
                    message_id = (headers.get("message-id", ("",))[0] or f"imap-{msg.uid}").strip().strip("<>")
                    attachments = [(a.filename, a.payload) for a in msg.attachments if a.filename and a.payload]
                    out.append((msg.uid, InboundEmail(
                        message_id=message_id, from_addr=from_addr, subject=msg.subject or "",
                        body_text=msg.text or msg.html or "", attachments=attachments,
                        seed_no=int(seed) if seed.isdigit() else None,
                        received_at=msg.date if msg.date else datetime.now(timezone.utc),
                    )))
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"gmail imap: {exc}") from exc
        return out

    def mark_processed(self, uids: list[str]) -> None:
        if not uids:
            return
        from imap_tools import MailBox

        try:
            with MailBox(self.s.gmail_imap_host).login(self.s.gmail_address, self.s.gmail_app_password,
                                                       initial_folder=self.s.gmail_label_inbox) as mb:
                if self.s.gmail_label_processed and self.s.gmail_label_processed != self.s.gmail_label_inbox:
                    try:
                        mb.folder.create(self.s.gmail_label_processed)
                    except Exception:  # noqa: BLE001 — exists
                        pass
                    mb.move(uids, self.s.gmail_label_processed)
        except Exception as exc:  # noqa: BLE001
            log.warning("gmail mark_processed failed: %s", exc)

    def archive_all(self) -> int:
        """Reset helper: move everything left in the inbox folder to processed. Returns count."""
        from imap_tools import MailBox

        try:
            with MailBox(self.s.gmail_imap_host).login(self.s.gmail_address, self.s.gmail_app_password,
                                                       initial_folder=self.s.gmail_label_inbox) as mb:
                uids = [m.uid for m in mb.fetch(headers_only=True, bulk=True)]
                if uids and self.s.gmail_label_processed != self.s.gmail_label_inbox:
                    mb.move(uids, self.s.gmail_label_processed)
                return len(uids)
        except Exception as exc:  # noqa: BLE001
            log.warning("gmail archive_all failed: %s", exc)
            return 0

    # --- send -----------------------------------------------------------------------------------------------
    def send_seed(self, inbound: InboundEmail, run_tag: str) -> str:
        """Send one seed email to the demo inbox. Returns the Message-ID used."""
        msg = EmailMessage()
        msg_id = make_msgid(idstring=f"seed-{inbound.seed_no or 0}-{run_tag}", domain="ops-agent.demo")
        msg["Message-ID"] = msg_id
        msg["From"] = self.s.gmail_address
        msg["To"] = self.s.gmail_address
        msg["Subject"] = inbound.subject
        msg["X-Ops-Agent-From"] = inbound.from_addr
        msg["X-Ops-Agent-Seed"] = str(inbound.seed_no or "")
        msg["X-Ops-Agent-Run"] = run_tag
        msg.set_content(inbound.body_text)
        for filename, data in inbound.attachments:
            maintype, subtype = _mime(filename)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        try:
            with smtplib.SMTP(self.s.gmail_smtp_host, self.s.gmail_smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(self.s.gmail_address, self.s.gmail_app_password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"gmail smtp: {exc}") from exc
        return msg_id.strip("<>")


def _mime(filename: str) -> tuple[str, str]:
    f = filename.lower()
    if f.endswith(".pdf"):
        return "application", "pdf"
    if f.endswith(".zip"):
        return "application", "zip"
    if f.endswith(".csv"):
        return "text", "csv"
    return "application", "octet-stream"
