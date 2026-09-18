"""Tiny in-process change signal for the page's SSE stream.

The pipeline runs in scheduler threads and request threads; the SSE endpoint runs on the event loop. Rather than
marshal every change across threads, we keep a monotonically increasing version number. Any write bumps it;
the /events generator polls it once a second and tells connected pages "something changed — refetch".
Also tracks a few flags the /status endpoint reports (resetting, seeding progress).
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_version = 0
_flags: dict[str, object] = {"resetting": False, "seeding": None, "last_error": None, "last_bump": time.time()}


def bump(note: str | None = None) -> int:
    global _version
    with _lock:
        _version += 1
        _flags["last_bump"] = time.time()
        if note:
            _flags["last_note"] = note
        return _version


def version() -> int:
    with _lock:
        return _version


def set_flag(key: str, value: object) -> None:
    with _lock:
        _flags[key] = value
    bump()


def flags() -> dict[str, object]:
    with _lock:
        return dict(_flags)
