"""Intake — turn an inbound email into `emails` + `documents` rows (the former n8n 01-intake flow).

Idempotent at two levels:
  * emails.gmail_message_id is UNIQUE — re-delivering the same message returns the existing row, creates nothing
  * documents UNIQUE(email_id, sha256) — the same file twice in one email yields one document

A file already seen on a *different* email is still recorded (the page must show it) but marked
status=ignore, duplicate_of=<first document> and its classification copied (Amendment A6).

Text extraction happens here so the source text is stored once and every later stage (and retries) reads it
from the row. Unreadable attachments get read_error set and are held by rules.evaluate.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.models import AppState, Document, Email
from agent.rules import RuleSet, sender_is_ignored
from agent.textextract import UnreadableAttachment, expand_zip, extract_text, is_zip, sniff_mime

log = logging.getLogger("ops_agent.intake")

BODY_FILENAME = "email-body.txt"


@dataclass
class InboundEmail:
    message_id: str
    from_addr: str
    subject: str
    body_text: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)
    seed_no: int | None = None
    received_at: datetime | None = None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest(session: Session, inbound: InboundEmail, rules: RuleSet) -> tuple[Email, bool]:
    """Returns (email, created). When created is False the message was seen before and nothing changed."""
    existing = session.scalar(select(Email).where(Email.gmail_message_id == inbound.message_id))
    if existing is not None:
        log.info("intake: %s already ingested as email #%s — skipping", inbound.message_id, existing.id)
        return existing, False

    state = session.get(AppState, 1)
    email = Email(
        gmail_message_id=inbound.message_id,
        seed_no=inbound.seed_no,
        from_addr=inbound.from_addr,
        subject=inbound.subject,
        body_text=inbound.body_text,
        run_id=state.current_run_id if state else None,
    )
    if inbound.received_at:
        email.received_at = inbound.received_at
    session.add(email)
    session.flush()

    ignored_by = sender_is_ignored(inbound.from_addr, rules)
    if ignored_by:
        doc = _body_document(email, inbound)
        doc.doc_type, doc.status, doc.reason = "ignore", "ignore", f"ignored sender domain (\"{ignored_by}\")"
        session.add(doc)
        email.ledger_status = "ignored"
        session.flush()
        return email, True

    files = _flatten_attachments(inbound.attachments)
    if not files:
        session.add(_body_document(email, inbound))
    else:
        seen_in_this_email: set[str] = set()
        for filename, data in files:
            digest = sha256(data)
            if digest in seen_in_this_email:
                continue
            seen_in_this_email.add(digest)
            doc = Document(email_id=email.id, filename=filename, sha256=digest, mime=sniff_mime(filename, data))
            try:
                doc.source_text = extract_text(filename, data)
            except UnreadableAttachment as exc:
                doc.read_error = str(exc)[:500]
                doc.source_text = None
            original = session.scalar(
                select(Document).where(Document.sha256 == digest, Document.email_id != email.id).order_by(Document.id).limit(1)
            )
            if original is not None:
                _mark_duplicate(doc, original)
            session.add(doc)
    session.flush()
    return email, True


def _body_document(email: Email, inbound: InboundEmail) -> Document:
    text = f"Subject: {inbound.subject}\n\n{inbound.body_text}".strip()
    return Document(email_id=email.id, filename=BODY_FILENAME, sha256=sha256(text.encode("utf-8")),
                    mime="text/plain", source_text=text)


def _flatten_attachments(attachments: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for filename, data in attachments:
        if is_zip(filename, data):
            try:
                out.extend(expand_zip(data))
            except UnreadableAttachment:
                out.append((filename, data))  # keep the zip itself so it surfaces as unreadable
        else:
            out.append((filename, data))
    return out


def _mark_duplicate(doc: Document, original: Document) -> None:
    doc.status = "ignore"
    doc.duplicate_of = original.id
    doc.reason = f"duplicate of doc #{original.id} (identical file, sha256 {original.sha256[:12]}…)"
    # copy what the original was so the page and ledger can show it without another model call
    doc.doc_type = original.doc_type
    doc.party_kind = original.party_kind
    doc.party_id = original.party_id
    doc.extracted = original.extracted
    doc.confidence = original.confidence
