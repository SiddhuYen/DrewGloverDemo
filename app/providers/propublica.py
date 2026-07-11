"""ProPublica Nonprofit Explorer — nonprofit board / officer co-membership.

A 990 filing lists an organisation's officers and directors, so two people on
the same nonprofit's board is a structurally asserted tie: `board_member`
(tier 2). This reaches the philanthropy layer — foundation boards where wealthy
and prominent figures actually sit together (the Gates Foundation, museum and
university boards) — which SEC filings and firm rosters never cover.

Free, no key. The JSON API gives only aggregate compensation, so the officer
NAMES come from the org page's `employee-row` markup. Rule 1 still applies: a
nonprofit whose board exceeds the cap materialises no pairwise edges.
"""
from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from .. import config
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_API = "https://projects.propublica.org/nonprofits/api/v2"
_ORG_PAGE = "https://projects.propublica.org/nonprofits/organizations/"
_LIMITER = IntervalLimiter(0.3)
_HEADERS = {"User-Agent": config.USER_AGENT}

_MAX_ORGS = 3
_MAX_OFFICERS = config.MAX_ROSTER_MEMBERS


class ProPublicaProvider:
    name = "propublica"

    def board_colleagues(self, name: str, org_hints: Optional[List[str]] = None
                         ) -> List[dict]:
        """Fellow officers/directors of nonprofits `name` is on.

        `org_hints` are organisations we already know the person by; each is
        searched on ProPublica, and only officer rosters that actually list the
        person are used (identity guard, same as firm rosters).

        Returns [{name, org, member_count, source_url, evidence}].
        """
        from ..edges.names import person_norm_key

        if not name or not org_hints:
            return []
        target = person_norm_key(name)
        key = cache.make_key(self.name, "board", target)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("rows", [])

        rows: List[dict] = []
        seen_eins = set()
        for hint in org_hints[:_MAX_ORGS]:
            for ein, org_name in self._search_orgs(hint):
                if ein in seen_eins:
                    continue
                seen_eins.add(ein)
                officers = self._officers(ein)
                names = {person_norm_key(o) for o in officers}
                if target not in names:
                    continue  # the person isn't on this board; assert nothing
                url = f"{_ORG_PAGE}{ein}"
                for officer in officers:
                    if person_norm_key(officer) == target:
                        continue
                    rows.append({
                        "name": officer, "org": org_name,
                        "member_count": len(officers), "source_url": url,
                        "evidence": (f"Both listed as officers/directors of "
                                     f"{org_name} on its IRS Form 990."),
                    })
                break  # one corroborated org per hint is enough
        cache.set(key, "board", {"rows": rows}, config.CACHE_TTL_WIKI)
        return rows

    # --- internals --------------------------------------------------------
    def _search_orgs(self, query: str):
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_API}/search.json", provider=self.name, headers=_HEADERS,
            params={"q": query})
        if resp is None or resp.status_code != 200:
            return []
        try:
            orgs = resp.json().get("organizations", [])
        except Exception:
            return []
        return [(str(o.get("ein")), o.get("name", "")) for o in orgs[:3]
                if o.get("ein")]

    def _officers(self, ein: str) -> List[str]:
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_ORG_PAGE}{ein}", provider=self.name, headers=_HEADERS)
        if resp is None or resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        from ..graph.builder import clean_person_names

        raw = []
        for row in soup.select("tr.employee-row"):
            cell = row.find("td")
            if not cell:
                continue
            # "Mark Suzman (Chief Executive Officer, Board Member)" -> "Mark Suzman"
            text = cell.get_text(" ", strip=True)
            text = re.split(r"\s*\(", text)[0].strip()
            if text:
                raw.append(text)
        return clean_person_names(raw)[:_MAX_OFFICERS]
