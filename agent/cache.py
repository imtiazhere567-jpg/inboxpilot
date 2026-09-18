"""Tiny in-process response cache for read endpoints.

Every write in the app calls events.bump(), so a cached value is valid exactly while events.version() is unchanged.
A short TTL on top refreshes the few time-derived fields (countdowns, "today" buckets) without a write happening.
Hosted deployments put the database in another region, where each round trip costs 100-300 ms; the dashboard needs
a dozen of them, so serving repeat loads from memory is the difference between 5 s and 50 ms.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable

from agent import events

_lock = threading.Lock()
_store: dict[Hashable, tuple[int, float, Any]] = {}


def cached(key: Hashable, ttl: float, compute: Callable[[], Any]) -> Any:
    version = events.version()
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] == version and hit[1] > now:
            return hit[2]
    value = compute()
    with _lock:
        _store[key] = (version, now + ttl, value)
        if len(_store) > 512:  # review items for many documents — keep it bounded
            for k in [k for k, v in _store.items() if v[0] != version][:256]:
                _store.pop(k, None)
    return value


def clear() -> None:
    with _lock:
        _store.clear()
