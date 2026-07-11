"""OpenAlex — co-authorship (free, no key).

A published work names its authors, so two people on the same paper is a
structurally asserted tie: `coauthor` (tier 2). Reaches the technical/academic
side of the startup world — researcher-founders, CTOs, professors-turned-VCs.

Identity guard (the namesake problem). "Vinod Khosla" resolves to TWO OpenAlex
authors — a prolific one and a one-paper author at Meta. Most academic papers
under a VC's name are not the VC. So an author is accepted only when their
OpenAlex institution corroborates an organisation we already know the person by;
without that corroboration we do not guess, exactly as the podcast silo does.
"""
from __future__ import annotations

from typing import List, Optional

from .. import config
from ..edges.names import normalize, person_norm_key
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_BASE = "https://api.openalex.org"
_LIMITER = IntervalLimiter(config.WIKI_MIN_INTERVAL)
_HEADERS = {"User-Agent": config.WIKI_USER_AGENT}

_MAX_WORKS = 10
_MAX_COAUTHORS = 25
# Generic org tokens carry no identifying power when corroborating identity.
_GENERIC = {"university", "college", "institute", "school", "inc", "llc",
            "ventures", "capital", "partners", "the", "of", "and", "company"}


def _org_tokens(orgs) -> set:
    return {tok for org in (orgs or [])
            for tok in normalize(org).split()
            if len(tok) > 3 and tok not in _GENERIC}


class OpenAlexProvider:
    name = "openalex"

    def coauthors(self, name: str, known_orgs: Optional[List[str]] = None
                  ) -> List[dict]:
        """People who published WITH `name`.

        Returns [{name, phrase, source_url}]. Empty unless an author whose
        institution corroborates a known org is found — a bare name match to a
        prolific academic namesake is discarded.
        """
        if not name:
            return []
        target = person_norm_key(name)
        org_tokens = _org_tokens(known_orgs)
        key = cache.make_key(self.name, "coauthors",
                             f"{target}::{'|'.join(sorted(org_tokens))}")
        cached = cache.get(key)
        if cached is not None:
            return cached.get("coauthors", [])

        author = self._resolve_author(name, target, org_tokens)
        out: List[dict] = []
        if author:
            out = self._coauthors_of(author["id"], target)
        cache.set(key, "coauthors", {"coauthors": out}, config.CACHE_TTL_WIKI)
        return out

    # --- internals --------------------------------------------------------
    def _resolve_author(self, name: str, target: str, org_tokens: set):
        """The OpenAlex author that is THIS person, or None.

        Requires corroboration: the author's institution must share a
        distinctive token with an org we know the person by. No org context
        (org_tokens empty) => we cannot disambiguate a namesake, so decline.
        """
        if not org_tokens:
            return None
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_BASE}/authors", provider=self.name, headers=_HEADERS,
            params={"search": name, "per_page": 10})
        if resp is None or resp.status_code != 200:
            return None
        try:
            results = resp.json().get("results", [])
        except Exception:
            return None
        for author in results:
            if person_norm_key(author.get("display_name", "")) != target:
                continue
            institutions = author.get("last_known_institutions") or []
            inst_text = normalize(" ".join(
                i.get("display_name", "") for i in institutions))
            if any(tok in inst_text for tok in org_tokens):
                return author
        return None

    def _coauthors_of(self, author_id: str, target: str) -> List[dict]:
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_BASE}/works", provider=self.name, headers=_HEADERS,
            params={"filter": f"author.id:{author_id.rsplit('/', 1)[-1]}",
                    "per_page": _MAX_WORKS,
                    "select": "id,title,authorships"})
        if resp is None or resp.status_code != 200:
            return []
        out, seen = [], set()
        try:
            works = resp.json().get("results", [])
        except Exception:
            return []
        for work in works:
            title = work.get("title") or ""
            url = work.get("id") or ""
            for au in work.get("authorships", []):
                coauthor = au.get("author", {}).get("display_name", "")
                k = person_norm_key(coauthor)
                if not k or k == target or k in seen:
                    continue
                seen.add(k)
                out.append({
                    "name": coauthor,
                    "phrase": "published with",
                    "source_url": url,
                    "evidence": f"Co-authors of “{title[:80]}” (OpenAlex).",
                })
                if len(out) >= _MAX_COAUTHORS:
                    return out
        return out
