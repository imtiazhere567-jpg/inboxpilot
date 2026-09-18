"""Deterministic party matching — runs BEFORE any model call and never uses the model.

Rules (seed/README.md):
  * identifier patterns are matched case-insensitively against the sender, subject, body and attachment text
  * a pattern ending in "-" is a reference prefix and must be followed by a digit (ACME-2031, not "acme-")
  * exactly one party hit -> matched; none -> none; two or more -> ambiguous
  * a party *name* appearing without any identifier -> name_only (never trusted on its own)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

from agent.schemas import MatchResult, PartyKind


@dataclass(frozen=True)
class Party:
    id: int
    name: str
    identifier_patterns: tuple[str, ...]


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    p = pattern.strip()
    if p.endswith("-"):
        return re.compile(r"(?<![A-Z0-9-])" + re.escape(p) + r"\d", re.IGNORECASE)
    return re.compile(re.escape(p), re.IGNORECASE)


def find_matches(text: str, parties: Iterable[Party]) -> dict[int, list[str]]:
    """party_id -> list of patterns that hit."""
    hits: dict[int, list[str]] = {}
    for party in parties:
        for pat in party.identifier_patterns:
            if _pattern_regex(pat).search(text):
                hits.setdefault(party.id, []).append(pat)
    return hits


def find_name_mentions(text: str, parties: Iterable[Party]) -> list[Party]:
    low = text.lower()
    return [p for p in parties if p.name.lower() in low]


def match_party(kind: PartyKind, text: str, parties: list[Party]) -> MatchResult:
    hits = find_matches(text, parties)
    by_id = {p.id: p for p in parties}
    if len(hits) == 1:
        (pid, pats), = hits.items()
        return MatchResult(status="matched", party_kind=kind, party_id=pid, party_name=by_id[pid].name, matched_on=pats)
    if len(hits) > 1:
        names = sorted(by_id[pid].name for pid in hits)
        return MatchResult(status="ambiguous", party_kind=kind, candidates=names,
                           matched_on=sorted({pat for pats in hits.values() for pat in pats}))
    mentioned = find_name_mentions(text, parties)
    if len(mentioned) == 1:
        p = mentioned[0]
        return MatchResult(status="name_only", party_kind=kind, party_id=p.id, party_name=p.name, candidates=[p.name])
    if len(mentioned) > 1:
        return MatchResult(status="ambiguous", party_kind=kind, candidates=sorted(p.name for p in mentioned))
    return MatchResult(status="none", party_kind=kind)


def match_any(text: str, suppliers: list[Party], customers: list[Party],
              prefer: Literal["supplier", "customer"] | None = None) -> MatchResult:
    """For documents whose type is unknown (unreadable attachment): try the preferred kind first, then the other."""
    order: list[tuple[PartyKind, list[Party]]] = [("supplier", suppliers), ("customer", customers)]
    if prefer == "customer":
        order.reverse()
    for kind, parties in order:
        r = match_party(kind, text, parties)
        if r.status != "none":
            return r
    return MatchResult(status="none", party_kind=None)
