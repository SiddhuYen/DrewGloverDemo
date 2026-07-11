"""Shared HTTP (retry + Retry-After) and the search-provider interface.

Structured providers (wikidata, edgar, opencorporates) call `request_with_retry`
directly. Web-search providers (serper, duckduckgo) subclass `SearchProvider`,
which gives them cache-first `search()` and a shared cache-first `fetch()`.

Web search is used ONLY to LOCATE roster / funding-announcement pages worth
scraping. It never mints an edge from a snippet — see edges/taxonomy.py Rule 0.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from .. import config
from . import cache
from .stats import STATS


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url,
                "snippet": self.snippet, "provider": self.provider}

    @staticmethod
    def from_dict(d: dict) -> "SearchResult":
        return SearchResult(d.get("title", ""), d.get("url", ""),
                            d.get("snippet", ""), d.get("provider", ""))


@dataclass
class Page:
    url: str
    content: str = ""          # raw HTML
    status_code: int = 0
    from_cache: bool = False
    meta: Dict[str, object] = field(default_factory=dict)


def make_client() -> httpx.Client:
    transport = httpx.HTTPTransport(retries=1)  # connection-level only
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=config.HTTP_TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    val = resp.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None  # HTTP-date form: ignore, fall back to backoff


def _sleep_backoff(attempt: int) -> None:
    if attempt < config.HTTP_RETRIES:
        time.sleep(config.HTTP_BACKOFF_BASE * (2 ** attempt))


def request_with_retry(method: str, url: str, *, provider: str,
                       limiter=None, breaker=None, **kwargs) -> Optional[httpx.Response]:
    """HTTP with provider rate limiting, circuit breaker, exponential backoff on
    429/5xx only, and Retry-After honoring. Returns None when every attempt
    fails or the breaker is open."""
    if breaker is not None and not breaker.allow():
        return None

    last: Optional[httpx.Response] = None
    for attempt in range(config.HTTP_RETRIES + 1):
        if limiter is not None:
            limiter.acquire()
        start = time.monotonic()
        try:
            with make_client() as c:
                resp = c.request(method, url, **kwargs)
        except Exception:
            if breaker is not None:
                breaker.record_failure()
            _sleep_backoff(attempt)
            continue
        STATS.record_call(provider, time.monotonic() - start)

        if resp.status_code in config.HTTP_RETRY_STATUS:
            last = resp
            if breaker is not None:
                breaker.record_failure()
            if attempt < config.HTTP_RETRIES:
                wait = _retry_after_seconds(resp)
                if wait is not None:
                    time.sleep(min(wait, 30.0))
                else:
                    _sleep_backoff(attempt)
            continue

        if breaker is not None:
            breaker.record_success()
        return resp
    return last


def fetch_page(url: str) -> Page:
    """Cache-first page fetch, shared across providers and keyed by URL."""
    key = cache.make_key("page", "fetch", url)
    cached = cache.get(key)
    if cached is not None:
        return Page(url=url, content=cached.get("content", ""),
                    status_code=cached.get("status_code", 0), from_cache=True)
    resp = request_with_retry("GET", url, provider="fetch")
    content, status = "", 0
    if resp is not None:
        status = resp.status_code
        if status == 200:
            # Keep the whole document, within reason. Portfolio grids sit far
            # down the markup; an 80 KB cut silently truncated Craft Ventures'
            # company list to zero.
            content = resp.text[: config.MAX_HTML_CHARS]
    cache.set(key, "page", {"content": content, "status_code": status},
              config.CACHE_TTL_PAGE)
    return Page(url=url, content=content, status_code=status, from_cache=False)


class SearchProvider(abc.ABC):
    name: str = "base"
    cache_ttl: int = config.CACHE_TTL_SEARCH

    @abc.abstractmethod
    def _search_uncached(self, query: str) -> List[SearchResult]:
        ...

    def available(self) -> bool:
        return True

    def search(self, query: str) -> List[SearchResult]:
        """Cache-first search. Never repeats an identical query within TTL."""
        key = cache.make_key(self.name, "search", query)
        cached = cache.get(key)
        if cached is not None:
            return [SearchResult.from_dict(d) for d in cached]
        results = self._search_uncached(query)
        # Never cache an empty result set. A provider returns [] both when a
        # query genuinely has no hits and when the request failed (403, breaker
        # open, quota exhausted); caching the latter for 30 days silently
        # disables the provider long after the cause is fixed.
        if results:
            cache.set(key, "search", [r.to_dict() for r in results], self.cache_ttl)
        return results

    def fetch(self, url: str) -> Page:
        return fetch_page(url)
