"""Brave Search API — used to LOCATE pages, never to assert edges.

Same contract as Serper and DuckDuckGo: given "Fiat Ventures team", Brave tells
us which URL to scrape. The roster on that page is the structural assertion; the
search snippet is not. Rule 0 is untouched by adding a search engine — nothing in
this module can create a relationship, and a result here only ever becomes an
edge if the page it points at structurally asserts one.

Key from BRAVE_API_KEY, or set at runtime from the app's Search tab. Absent =>
unavailable, and the caller falls back exactly as before.

Brave's free tier is 1 query/second; the limiter below is not politeness, it is
the documented rate and exceeding it returns 429.
"""
from __future__ import annotations

import threading
import time
from typing import List

from .. import config
from .base import SearchProvider, SearchResult, make_client
from .ratelimit import IntervalLimiter
from .stats import STATS

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(SearchProvider):
    name = "brave"
    cache_ttl = config.CACHE_TTL_SEARCH

    def __init__(self) -> None:
        interval = 1.0 / config.BRAVE_QPS if config.BRAVE_QPS > 0 else 0.0
        self._limiter = IntervalLimiter(interval)
        self._lock = threading.Lock()
        self._exhausted = False

    def available(self) -> bool:
        return bool(config.BRAVE_API_KEY) and not self._exhausted

    def _search_uncached(self, query: str) -> List[SearchResult]:
        if not self.available():
            return []
        self._limiter.acquire()
        start = time.monotonic()
        try:
            with make_client() as c:
                resp = c.get(
                    BRAVE_ENDPOINT,
                    headers={"X-Subscription-Token": config.BRAVE_API_KEY,
                             "Accept": "application/json"},
                    params={"q": query, "count": config.RESULTS_PER_QUERY},
                )
        except Exception:
            return []
        STATS.record_call(self.name, time.monotonic() - start)

        if resp.status_code in (401, 403):
            # A bad key must not be retried for every query in the run.
            self._exhausted = True
            return []
        if resp.status_code in (429, 402):
            self._exhausted = True
            return []
        if resp.status_code != 200:
            return []

        out: List[SearchResult] = []
        try:
            for item in (resp.json().get("web", {}) or {}).get("results", []) or []:
                url = item.get("url")
                if not url:
                    continue
                out.append(SearchResult(
                    title=item.get("title", "") or "",
                    url=url,
                    snippet=item.get("description", "") or "",
                    provider=self.name,
                ))
        except Exception:
            return []
        return out


def brave_status() -> dict:
    """Shape mirrors serper_status() so /health can report both the same way."""
    if not config.BRAVE_API_KEY:
        return {"ok": False, "state": "not_configured"}
    return {"ok": True, "state": "ready"}


def set_key(key: str) -> bool:
    """Install a key at runtime, from the UI.

    The packaged app has no terminal to export an env var in and no project root
    to drop a .env into, so a key typed into the window is the only route a user
    actually has. Returns False for an obviously wrong value rather than letting
    every later search fail silently with a 401.
    """
    key = (key or "").strip()
    if not key:
        config.BRAVE_API_KEY = ""
        return True
    if len(key) < 8 or " " in key:
        return False
    config.BRAVE_API_KEY = key
    return True
