"""Turn attachment bytes into text the model can read. No OCR — scanned images are out of scope (PLAN §1).

Raises UnreadableAttachment for anything that cannot be parsed; the pipeline turns that into a held item
with reason "could not read attachment" (seed #16).
"""
from __future__ import annotations

import io
import zipfile

from pypdf import PdfReader

MAX_TEXT_CHARS = 20_000


class UnreadableAttachment(Exception):
    pass


def sniff_mime(filename: str, data: bytes) -> str:
    name = filename.lower()
    if data.startswith(b"%PDF") or name.endswith(".pdf"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04") or name.endswith(".zip"):
        return "application/zip"
    if name.endswith(".csv"):
        return "text/csv"
    if name.endswith((".txt", ".md")):
        return "text/plain"
    return "application/octet-stream"


def is_zip(filename: str, data: bytes) -> bool:
    return sniff_mime(filename, data) == "application/zip"


def expand_zip(data: bytes) -> list[tuple[str, bytes]]:
    """Flatten a ZIP into (member_filename, bytes). Directories and hidden/system members are skipped."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            out = []
            for info in zf.infolist():
                if info.is_dir() or info.filename.startswith(("__MACOSX/", ".")):
                    continue
                out.append((info.filename.split("/")[-1], zf.read(info)))
            return out
    except zipfile.BadZipFile as exc:
        raise UnreadableAttachment(f"bad zip: {exc}") from exc


def extract_text(filename: str, data: bytes) -> str:
    mime = sniff_mime(filename, data)
    try:
        if mime == "application/pdf":
            reader = PdfReader(io.BytesIO(data))
            pages = [p.extract_text() or "" for p in reader.pages]
            text = "\n".join(pages).strip()
            if not text:
                raise UnreadableAttachment("PDF contains no extractable text (scanned image?)")
            return text[:MAX_TEXT_CHARS]
        if mime in ("text/csv", "text/plain"):
            return data.decode("utf-8-sig", errors="replace")[:MAX_TEXT_CHARS]
    except UnreadableAttachment:
        raise
    except Exception as exc:  # pypdf raises a zoo of exception types for corrupt input
        raise UnreadableAttachment(f"{type(exc).__name__}: {exc}") from exc
    raise UnreadableAttachment(f"unsupported attachment type: {mime}")
