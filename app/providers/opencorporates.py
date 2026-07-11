"""OpenCorporates — company officer / director networks.

Reaches people who will never appear on Wikidata: resolve a name to its officer
positions, then pull the co-officers of those companies. An officer record is a
filed corporate registration, so it satisfies Rule 0.

Requires OPENCORPORATES_API_TOKEN (free tier). Absent => the provider no-ops
cleanly, which is why the demo does not depend on it.
"""
from __future__ import annotations

from typing import List

from .. import config
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_BASE = "https://api.opencorporates.com/v0.4"
_LIMITER = IntervalLimiter(config.OPENCORP_MIN_INTERVAL)

_MAX_COMPANIES = 3
_MAX_OFFICERS_PER_COMPANY = 20
_MAX_TOTAL = 30


def _relationship_for(position: str) -> str:
    p = (position or "").lower()
    if any(k in p for k in ("director", "board", "trustee", "chair")):
        return "board_member"
    return "colleague"


class OpenCorporatesProvider:
    name = "opencorporates"

    def available(self) -> bool:
        return bool(config.OPENCORPORATES_API_TOKEN)

    def officer_colleagues(self, name: str) -> List[dict]:
        """Co-officers of companies where `name` is an officer.
        Returns [{name, relationship_type, org, org_type, member_count,
                  source_url, evidence}]."""
        if not name or not self.available():
            return []
        key = cache.make_key(self.name, "colleagues", name.lower())
        cached = cache.get(key)
        if cached is not None:
            return cached.get("colleagues", [])

        results: List[dict] = []
        seen = {name.lower()}
        for jur, num, company in self._companies_for_officer(name)[:_MAX_COMPANIES]:
            officers = self._company_officers(jur, num)
            url = f"https://opencorporates.com/companies/{jur}/{num}"
            for off_name, position in officers:
                k = off_name.lower()
                if k in seen:
                    continue
                seen.add(k)
                rel = _relationship_for(position)
                results.append({
                    "name": off_name,
                    "relationship_type": rel,
                    "org": company,
                    "org_type": "company",
                    "member_count": len(officers),
                    "source_url": url,
                    "evidence": (f"Both registered as officers of {company}"
                                 f"{f' ({position})' if position else ''}."),
                })
                if len(results) >= _MAX_TOTAL:
                    break
            if len(results) >= _MAX_TOTAL:
                break
        cache.set(key, "colleagues", {"colleagues": results}, config.CACHE_TTL_WIKI)
        return results

    # --- internals --------------------------------------------------------
    def _companies_for_officer(self, name: str):
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_BASE}/officers/search", provider=self.name,
            params={"q": name, "per_page": 20,
                    "api_token": config.OPENCORPORATES_API_TOKEN},
        )
        if resp is None or resp.status_code != 200:
            return []
        out = []
        try:
            for item in resp.json().get("results", {}).get("officers", []) or []:
                comp = (item.get("officer", {}) or {}).get("company", {}) or {}
                jur, num = comp.get("jurisdiction_code"), comp.get("company_number")
                if jur and num:
                    out.append((jur, num, comp.get("name", "")))
        except Exception:
            return []
        return out

    def _company_officers(self, jurisdiction: str, number: str):
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", f"{_BASE}/companies/{jurisdiction}/{number}", provider=self.name,
            params={"api_token": config.OPENCORPORATES_API_TOKEN},
        )
        if resp is None or resp.status_code != 200:
            return []
        out = []
        try:
            company = resp.json().get("results", {}).get("company", {}) or {}
            for item in (company.get("officers") or [])[:_MAX_OFFICERS_PER_COMPANY]:
                off = item.get("officer", {}) or {}
                if off.get("name"):
                    out.append((off["name"], off.get("position", "")))
        except Exception:
            return []
        return out
