"""Shared bits for the external-system clients.

Every integration exposes the same small surface:
    configured: bool           credentials present?
    create_*(...) -> str       returns the external id
    delete(external_id)        best-effort cleanup used by the hourly reset

When an integration is NOT configured the actions pipeline records the action as *simulated* (external id
"sim-<system>-<n>", result {"simulated": true}) so the demo still shows the full flow on a laptop with no
sandbox accounts. The page labels those clearly. A SimulatedOutage is raised when the rules sheet says
simulate_outage=<system> so the Failed / Retry path can be demonstrated on demand (Amendment A4).
"""
from __future__ import annotations

import itertools
import threading


class IntegrationError(Exception):
    """A call to an external system failed. Message is safe to show on the page."""


class SimulatedOutage(IntegrationError):
    def __init__(self, system: str) -> None:
        super().__init__(f"{system} unavailable (simulated outage via rules sheet)")
        self.system = system


_counter = itertools.count(1)
_lock = threading.Lock()


def simulated_id(system: str) -> str:
    with _lock:
        return f"sim-{system}-{next(_counter)}"
