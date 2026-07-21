"""Trestle Reverse Phone lookup — resolves a bare number to its owner's NAME.

Used by ONE caller: the vCard address-book import (app/ingest/vcard.py). An
iPhone contact can be saved as a phone number with no name attached; without a
name it cannot become a named graph node. Given such a number, Trestle's
Reverse Phone API (https://trestleiq.com) returns the owner's name so the
contact still lands in Drew's first degree as a real person.

Rule 0 is untouched: the vCard entry is the structural assertion that Drew knows
this contact. Trestle never creates a relationship — it only supplies a label
for a node the address book already placed in the first degree, exactly as
Serper only LOCATES a page it never asserts an edge from.

Key from TRESTLE_API_KEY; absent => `is_active()` is False and every lookup
returns None (the caller then uses an "Unknown (<number>)" placeholder).
Cache-first and positive-only: a resolved number is cached for the standard TTL
so a re-import never re-spends, and a miss/failure is never cached (mirrors the
serper policy, so a transient outage can't disable a number for 30 days).
"""
from __future__ import annotations

from typing import Optional

from .. import config
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_limiter = IntervalLimiter(config.TRESTLE_MIN_INTERVAL)


def is_active() -> bool:
    return config.TRESTLE_ENABLED and bool(config.TRESTLE_API_KEY)


def normalize_number(raw: str) -> str:
    """Best-effort E.164 form, US-defaulted. Keeps a leading '+', drops every
    other non-digit, and supplies +1 for a bare 10-digit US number so the same
    contact written "(555) 123-4567" and "+1 555-123-4567" dedupes to one key."""
    s = (raw or "").strip()
    if not s:
        return ""
    plus = s.startswith("+")
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    if plus:
        return "+" + digits
    if len(digits) == 10 and config.TRESTLE_COUNTRY_HINT == "US":
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def _best_owner_name(payload: dict) -> str:
    """Pull the most likely person name out of a Reverse Phone response.

    The API shape has drifted across versions (`owners`, `belongs_to`, a
    top-level `name`); try each, prefer an entry typed as a person over a
    business, and fall back to the first named entry either way. Kept pure so
    it is unit-testable without a live key.
    """
    if not isinstance(payload, dict):
        return ""
    candidates = []
    for field in ("owners", "belongs_to"):
        val = payload.get(field)
        if isinstance(val, list):
            candidates.extend(v for v in val if isinstance(v, dict))
        elif isinstance(val, dict):
            candidates.append(val)

    def name_of(entry: dict) -> str:
        name = (entry.get("name") or "").strip()
        if name:
            return name
        first = (entry.get("firstname") or entry.get("first_name") or "").strip()
        last = (entry.get("lastname") or entry.get("last_name") or "").strip()
        return " ".join(p for p in (first, last) if p).strip()

    def is_person(entry: dict) -> bool:
        return str(entry.get("type") or "").lower() in ("person", "people", "")

    for entry in candidates:
        if is_person(entry) and name_of(entry):
            return name_of(entry)
    for entry in candidates:
        if name_of(entry):
            return name_of(entry)
    return (payload.get("name") or "").strip()


def reverse_phone(number: str) -> Optional[str]:
    """Owner name for a phone number, or None when unavailable/unresolved.

    Cache-first; only a positive hit is cached. Fails soft: no key, a bad
    response, or any transport error all return None so an import never breaks
    on the lookup step.
    """
    norm = normalize_number(number)
    if not norm or not is_active():
        return None

    key = cache.make_key("trestle", "reverse_phone", norm)
    cached = cache.get(key)
    if cached is not None:
        return cached.get("name") or None

    _limiter.acquire()
    resp = request_with_retry(
        "GET", config.TRESTLE_ENDPOINT, provider="trestle",
        headers={"x-api-key": config.TRESTLE_API_KEY, "Accept": "application/json"},
        params={"phone": norm, "phone.country_hint": config.TRESTLE_COUNTRY_HINT},
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        name = _best_owner_name(resp.json())
    except Exception:
        return None
    if not name:
        return None
    cache.set(key, "trestle", {"name": name}, config.TRESTLE_CACHE_TTL)
    return name
