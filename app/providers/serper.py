"""Serper.dev (Google SERP) — used to LOCATE pages, never to assert edges.

Given "Fiat Ventures team", Serper tells us which URL to scrape. The roster on
that page is the structural assertion; the search snippet is not. Nothing in
this module ever creates a relationship.

Key from SERPER_API_KEY; absent => unavailable and the caller falls back to
DuckDuckGo. Monthly quota persisted in the cache DB so it survives restarts.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import List

from .. import config
from . import cache
from .base import SearchProvider, SearchResult, make_client
from .ratelimit import IntervalLimiter
from .stats import STATS


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _state_key() -> str:
    return cache.make_key("serperstate", _current_month(), "s")


def _mark_state(state: str) -> None:
    try:
        cache.set(_state_key(), "serperstate", {"state": state}, 40 * 86400)
    except Exception:
        pass


def set_key(key: str) -> bool:
    """Install a Serper key at runtime, from the UI.

    The packaged app has no terminal to export SERPER_API_KEY in and no project
    root to drop a .env into, so a key typed into the window is the only route a
    user of the .exe actually has.

    Clearing a persisted `invalid_key` / `exhausted` state matters: those are
    sticky by design so one bad key does not re-fail every query in a run, but a
    NEW key deserves a fresh verdict rather than inheriting the old one's.
    """
    key = (key or "").strip()
    if key and (len(key) < 8 or " " in key):
        return False
    config.SERPER_API_KEY = key
    _mark_state("")
    return True


def serper_status() -> dict:
    """Availability for /health: monthly usage plus any outage state.
    state is one of: ok | exhausted | invalid_key | not_configured."""
    used = cache.get_counter(cache.make_key("serperquota", _current_month(), "count"))
    quota = config.SERPER_MONTHLY_QUOTA
    persisted = (cache.get(_state_key(), track=False) or {}).get("state")
    if not config.SERPER_API_KEY:
        state = "not_configured"
    elif persisted in ("exhausted", "invalid_key"):
        state = persisted
    elif used >= quota:
        state = "exhausted"
    else:
        state = "ok"
    return {"ok": state == "ok", "state": state,
            "used": used, "quota": quota, "remaining": max(0, quota - used)}


class SerperProvider(SearchProvider):
    name = "serper"
    cache_ttl = config.CACHE_TTL_SEARCH

    def __init__(self) -> None:
        interval = 1.0 / config.SERPER_QPS if config.SERPER_QPS > 0 else 0.0
        self._limiter = IntervalLimiter(interval)
        self._lock = threading.Lock()
        self._quota_key = cache.make_key("serperquota", _current_month(), "count")
        self._used = cache.get_counter(self._quota_key)
        self._exhausted = False

    def available(self) -> bool:
        return (bool(config.SERPER_API_KEY) and not self._exhausted
                and self._used < config.SERPER_MONTHLY_QUOTA)

    def _search_uncached(self, query: str) -> List[SearchResult]:
        if not self.available():
            return []
        self._limiter.acquire()
        with self._lock:
            self._used = cache.incr_counter(self._quota_key)
        start = time.monotonic()
        try:
            with make_client() as c:
                resp = c.post(
                    config.SERPER_ENDPOINT,
                    headers={"X-API-KEY": config.SERPER_API_KEY,
                             "Content-Type": "application/json"},
                    content=json.dumps({"q": query, "num": config.RESULTS_PER_QUERY}),
                )
        except Exception:
            return []
        STATS.record_call(self.name, time.monotonic() - start)

        if resp.status_code in (401, 403):
            self._exhausted = True          # bad/expired key
            _mark_state("invalid_key")
            return []
        if resp.status_code in (429, 402):
            self._exhausted = True          # rate/quota/credit exhausted
            _mark_state("exhausted")
            return []
        if resp.status_code != 200:
            return []

        out: List[SearchResult] = []
        try:
            for item in (resp.json().get("organic", []) or []):
                title, url = item.get("title") or "", item.get("link") or ""
                if url and title:
                    out.append(SearchResult(title, url, item.get("snippet") or "",
                                            self.name))
        except Exception:
            return []
        return out[: config.RESULTS_PER_QUERY]
