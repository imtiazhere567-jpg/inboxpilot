"""In-process cache for read endpoints, kept warm in the background.

Every write in the app calls events.bump(), so a cached value is valid exactly while events.version() is unchanged.
A long TTL on top only refreshes time-derived fields (countdowns, "today" buckets). A warmer thread recomputes the
standard entries whenever the version changes, so a person almost never waits for the database.
Hosted deployments put Postgres in another region, where each round trip costs 100-500 ms and the dashboard needs
a dozen: from memory the same page answers in a few milliseconds.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Hashable

from agent import events

log = logging.getLogger("ops_agent.cache")

_lock = threading.Lock()
_store: dict[Hashable, tuple[int, float, Any]] = {}
_warmers: dict[Hashable, tuple[float, Callable[[], Any]]] = {}   # key -> (ttl, compute) to keep warm
_warmed_version = -1
_warmed_at = 0.0
_stop = threading.Event()


def cached(key: Hashable, ttl: float, compute: Callable[[], Any], keep_warm: bool = True) -> Any:
    version = events.version()
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] == version and hit[1] > now:
            return hit[2]
    value = compute()
    with _lock:
        _store[key] = (version, now + ttl, value)
        if keep_warm:
            _warmers[key] = (ttl, compute)
        if len(_store) > 512:  # review items for many documents — keep it bounded
            for k in [k for k, v in _store.items() if v[0] != version][:256]:
                _store.pop(k, None)
    return value


def clear() -> None:
    global _warmed_version
    with _lock:
        _store.clear()
        _warmers.clear()
        _warmed_version = -1


def _warm_once() -> None:
    global _warmed_version, _warmed_at
    version = events.version()
    now = time.monotonic()
    with _lock:
        todo = [(k, ttl, fn) for k, (ttl, fn) in _warmers.items()
                if k not in _store or _store[k][0] != version or _store[k][1] - now < 60]
    for key, ttl, fn in todo:
        if events.version() != version:
            return  # something changed mid-way; the next pass starts over
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 — a warm miss is not an error for the page
            log.debug("warm %s failed: %s", key, exc)
            continue
        with _lock:
            _store[key] = (version, time.monotonic() + ttl, value)
    _warmed_version, _warmed_at = version, now


def _warm_loop(interval: float) -> None:
    while not _stop.wait(interval):
        try:
            _warm_once()
        except Exception as exc:  # noqa: BLE001
            log.debug("warm loop: %s", exc)


def start_warmer(interval: float = 1.0) -> threading.Thread:
    """Recompute registered entries shortly after every change (and before they expire)."""
    _stop.clear()
    t = threading.Thread(target=_warm_loop, args=(interval,), name="cache-warmer", daemon=True)
    t.start()
    return t


def stop_warmer() -> None:
    _stop.set()
