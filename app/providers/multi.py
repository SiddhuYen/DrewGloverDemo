"""Every available search engine at once, merged.

The old chain picked exactly ONE engine — Serper if keyed, else DuckDuckGo — so a
target the graph had never seen was found or missed on the strength of that one
engine's index. Engines disagree: Brave surfaces roster pages DuckDuckGo buries,
and DuckDuckGo has no key to exhaust. Running them together costs no extra wall
clock, because they run in parallel and the slowest one sets the pace.

This changes only WHICH PAGES get read. Rule 0 is untouched: a search result is a
URL to fetch, never an assertion, and every edge still comes from a page that
structurally asserts it. More engines means more pages considered, never a lower
bar for what counts as a relationship.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from .base import SearchProvider, SearchResult


class MultiSearchProvider(SearchProvider):
    """Fan a query out to every available provider; merge, dedupe, interleave."""

    name = "multi"

    def __init__(self, providers: List[SearchProvider]) -> None:
        self._providers = [p for p in providers if p.available()]

    def available(self) -> bool:
        return bool(self._providers)

    @property
    def engines(self) -> List[str]:
        return [p.name for p in self._providers]

    def search(self, query: str) -> List[SearchResult]:
        """Override `search`, not `_search_uncached`.

        Each provider owns its own cache entry under its own name, so caching
        again at this level would double-store every result and, worse, freeze the
        merged set from whichever engines happened to be configured on the first
        run of a query.
        """
        if not self._providers:
            return []
        if len(self._providers) == 1:
            return self._providers[0].search(query)

        per_engine: List[List[SearchResult]] = []
        with ThreadPoolExecutor(max_workers=len(self._providers)) as pool:
            futures = {pool.submit(p.search, query): p for p in self._providers}
            for fut in as_completed(futures):
                try:
                    per_engine.append(fut.result() or [])
                except Exception:
                    # One engine erroring, rate-limiting or tripping its breaker
                    # must not lose the others' results.
                    per_engine.append([])

        # Interleave by rank rather than concatenating: an engine's first hit is
        # its best guess, and concatenation would bury every engine after the
        # first behind the whole of its predecessor's page.
        merged: List[SearchResult] = []
        seen = set()
        for rank in range(max((len(r) for r in per_engine), default=0)):
            for results in per_engine:
                if rank >= len(results):
                    continue
                r = results[rank]
                key = (r.url or "").rstrip("/").lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(r)
        return merged

    def _search_uncached(self, query: str) -> List[SearchResult]:  # pragma: no cover
        # Unreachable: `search` is overridden above and never delegates here.
        raise NotImplementedError
