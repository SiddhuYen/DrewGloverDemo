"""Wikipedia provider — title -> QID resolution and a notability signal.

Its only job here is to hand a Wikidata QID to WikidataProvider, which is where
the structured claims live. Light rate limiter; every response cached.
"""
from __future__ import annotations

from typing import List, Optional

from bs4 import BeautifulSoup

from .. import config
from ..edges.names import person_norm_key
from . import cache
from .base import SearchProvider, SearchResult, request_with_retry
from .ratelimit import IntervalLimiter

_API = "https://en.wikipedia.org/w/api.php"
_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_LIMITER = IntervalLimiter(config.WIKI_MIN_INTERVAL)
# Wikimedia rejects the default spoofed-browser UA with a 403.
_HEADERS = {"User-Agent": config.WIKI_USER_AGENT}


class WikipediaProvider(SearchProvider):
    name = "wikipedia"
    cache_ttl = config.CACHE_TTL_WIKI

    def _search_uncached(self, query: str) -> List[SearchResult]:
        _LIMITER.acquire()
        params = {"action": "query", "list": "search", "srsearch": query,
                  "format": "json", "srlimit": config.RESULTS_PER_QUERY}
        resp = request_with_retry("GET", _API, provider=self.name, params=params,
                                  headers=_HEADERS)
        out: List[SearchResult] = []
        if resp is not None and resp.status_code == 200:
            try:
                for hit in resp.json().get("query", {}).get("search", []):
                    title = hit.get("title", "")
                    snippet = _strip_html(hit.get("snippet", ""))
                    url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                    out.append(SearchResult(title, url, snippet, self.name))
            except Exception:
                pass
        return out

    def summary(self, title: str) -> str:
        key = cache.make_key(self.name, "summary", title)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("text", "")
        _LIMITER.acquire()
        resp = request_with_retry("GET", _SUMMARY + title.replace(" ", "_"),
                                  provider=self.name, headers=_HEADERS)
        if resp is None or resp.status_code != 200:
            return ""  # a transport failure is not a fact; never cache it
        try:
            text = resp.json().get("extract", "") or ""
        except Exception:
            return ""
        cache.set(key, "summary", {"text": text}, self.cache_ttl)
        return text

    def wikidata_id(self, title: str) -> Optional[str]:
        key = cache.make_key(self.name, "wdid", title)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("qid")
        _LIMITER.acquire()
        params = {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                  "titles": title, "format": "json", "redirects": 1}
        resp = request_with_retry("GET", _API, provider=self.name, params=params,
                                  headers=_HEADERS)
        # Distinguish "this person genuinely has no QID" (cacheable) from "the
        # request failed" (must not be cached). Caching a 403 as `qid: None` for
        # 30 days silently disabled the entire Wikidata backbone.
        if resp is None or resp.status_code != 200:
            return None
        qid = None
        try:
            for page in resp.json().get("query", {}).get("pages", {}).values():
                qid = page.get("pageprops", {}).get("wikibase_item")
                if qid:
                    break
        except Exception:
            return None
        cache.set(key, "wdid", {"qid": qid}, self.cache_ttl)
        return qid

    def best_title(self, name: str, hint: str = "") -> Optional[str]:
        """The search hit whose TITLE IS the person's name, or None. `hint`
        (e.g. "biotech founder") is added to the QUERY to rank the right namesake
        first — the title-equals-name guard below still prevents a wrong match.

        Never trust the top hit blindly. Wikipedia's first result for
        "Drew Glover" is a page whose Wikidata entity is Nikolas Cruz — a human,
        so an `is_human` check passes — and stamping Drew's node with that QID
        would merge him with a mass shooter. The title must match the queried
        name on the normalized person key.
        """
        target = person_norm_key(name)
        if not target:
            return None
        query = f"{name} {hint.strip()}" if hint and hint.strip() else name
        for result in self.search(query):
            # "Charles Hudson (disambiguation)" and "The Pitch (podcast)" both
            # fail this, which is exactly right.
            if person_norm_key(result.title) == target:
                return result.title
        return None

    def qid_for_name(self, name: str, hint: str = "") -> Optional[str]:
        """QID of the page that is unambiguously about `name`, or None."""
        title = self.best_title(name, hint=hint)
        return self.wikidata_id(title) if title else None


def _strip_html(s: str) -> str:
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
