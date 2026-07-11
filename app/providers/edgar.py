"""SEC EDGAR — board / insider co-membership from Form 4 filings.

Free, no key; SEC requires a declared User-Agent with real contact info.

A Form 4 filing structurally names two parties: the reporting insider and the
issuer. So:
    person -> their Form 4 filings -> issuer CIKs
    issuer CIK -> that issuer's Form 4 filers -> co-insiders (fellow
                  directors / officers / 10% owners)

We resolve issuers by CIK rather than by company-name string, because the CIK
is in the same filing that named the person — no fuzzy re-matching, no chance
of pulling a different company that merely shares a name.

Co-insiders are `board_member` (tier 2): Form 4 filers are a company's
directors, officers, and principal owners.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .. import config
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_FTS = "https://efts.sec.gov/LATEST/search-index"
_LIMITER = IntervalLimiter(config.EDGAR_MIN_INTERVAL)

_CIK_RE = re.compile(r"\(CIK (\d{10})\)")
_TICKER_RE = re.compile(r"\([A-Z]{1,6}\)")

# Generational suffixes are not part of the dedup key: EDGAR writes
# "Hudson Charles E. III" where other sources write "Charles Hudson".
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_MAX_COMPANIES = 3
_MAX_INSIDERS_PER_COMPANY = 30
_MAX_TOTAL = 40


def _clean(display_name: str) -> str:
    """'MICROSOFT CORP  (MSFT)  (CIK 0000789019)' -> 'MICROSOFT CORP'."""
    return display_name.split("  (")[0].strip()


def _cik(display_name: str) -> str:
    m = _CIK_RE.search(display_name)
    return m.group(1) if m else ""


def _is_company(display_name: str) -> bool:
    # A ticker is decisive. Otherwise fall back to an org-suffix check.
    if _TICKER_RE.search(display_name):
        return True
    from ..edges.names import looks_like_org_name
    return looks_like_org_name(_clean(display_name))


def _person_display(display_name: str) -> str:
    """EDGAR writes 'LAST FIRST MIDDLE [SUFFIX]' -> render 'First Middle Last'."""
    parts = _clean(display_name).split()
    suffix = ""
    if parts and parts[-1].rstrip(".").lower() in _NAME_SUFFIXES:
        suffix = parts.pop().rstrip(".")
    if len(parts) >= 2:
        parts = parts[1:] + parts[:1]
    name = " ".join(parts).title()
    return f"{name} {suffix.title()}".strip() if suffix else name


def _filed_by(display_names: List[str], target_key: str) -> bool:
    """True iff `target_key` is one of the REPORTING PEOPLE on this filing.

    EDGAR's full-text search matches a name appearing anywhere in a document,
    so a hit alone proves nothing. Without this check, querying "Charles Hudson"
    returns a Form 4 filed by "Hudson Charles E. III" — an unrelated Joby
    Aviation insider — and we would wire a seed-stage VC into Joby's board.
    That is a fabricated bridge, which Rule 0 exists to make impossible.
    """
    from ..edges.names import person_norm_key

    for dn in display_names:
        if _is_company(dn):
            continue
        if person_norm_key(_person_display(dn)) == target_key:
            return True
    return False


class EdgarProvider:
    name = "edgar"

    def available(self) -> bool:
        return bool(config.EDGAR_ENABLED)

    def issuers_for_person(self, name: str) -> List[Tuple[str, str]]:
        """[(issuer_cik, issuer_name)] where `name` HIMSELF filed a Form 4.

        A hit is used only when the queried person is one of the filing's
        reporting people — see `_filed_by`. A name that merely appears in the
        document text is not an insider, and must not become one.
        """
        if not name or not self.available():
            return []
        from ..edges.names import person_norm_key

        target_key = person_norm_key(name)
        if not target_key:
            return []
        key = cache.make_key(self.name, "issuers", target_key)
        cached = cache.get(key)
        if cached is not None:
            return [tuple(x) for x in cached.get("issuers", [])]

        # EDGAR's full-text index stores reporting people as "LAST FIRST MIDDLE".
        # A quoted search for the display form "Donald Trump" returns 0 hits;
        # "Trump Donald" returns 26. So query BOTH the display form and the
        # inverted form and union the results. `_filed_by` still gates each hit,
        # so widening the search cannot admit a name that isn't on the filing.
        queries = {f'"{name}"'}
        parts = name.split()
        if len(parts) >= 2:
            queries.add(f'"{parts[-1]} {" ".join(parts[:-1])}"')

        out: List[Tuple[str, str]] = []
        seen = set()
        hits = [h for q in queries for h in self._hits(q=q, forms="4")]
        for hit in hits:
            display_names = hit.get("_source", {}).get("display_names", [])
            if not _filed_by(display_names, target_key):
                continue  # the person is named in the text, not on the filing
            for dn in display_names:
                if not _is_company(dn):
                    continue
                cik, cname = _cik(dn), _clean(dn)
                if cik and cik not in seen:
                    seen.add(cik)
                    out.append((cik, cname))
        out = out[:_MAX_COMPANIES]
        cache.set(key, "issuers", {"issuers": [list(t) for t in out]},
                  config.CACHE_TTL_WIKI)
        return out

    def insiders_for_issuer(self, cik: str) -> List[str]:
        """Names of people who filed Form 4s against this issuer CIK."""
        if not cik or not self.available():
            return []
        key = cache.make_key(self.name, "insiders", cik)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("insiders", [])

        people: List[str] = []
        seen = set()
        for hit in self._hits(q="", forms="4", ciks=cik):
            for dn in hit.get("_source", {}).get("display_names", []):
                if _is_company(dn):
                    continue
                person = _person_display(dn)
                k = person.lower()
                if person and k not in seen:
                    seen.add(k)
                    people.append(person)
                if len(people) >= _MAX_INSIDERS_PER_COMPANY:
                    break
            if len(people) >= _MAX_INSIDERS_PER_COMPANY:
                break
        cache.set(key, "insiders", {"insiders": people}, config.CACHE_TTL_WIKI)
        return people

    def board_colleagues(self, name: str) -> List[dict]:
        """Fellow Form 4 filers at companies where `name` is an insider.

        Returns [{name, relationship_type, org, org_type, member_count,
                  source_url, evidence}].
        """
        results: List[dict] = []
        seen = {name.lower()}
        for cik, company in self.issuers_for_person(name):
            insiders = self.insiders_for_issuer(cik)
            url = (f"https://www.sec.gov/cgi-bin/browse-edgar"
                   f"?action=getcompany&CIK={cik}&type=4")
            for person in insiders:
                k = person.lower()
                if k in seen:
                    continue
                seen.add(k)
                results.append({
                    "name": person,
                    "relationship_type": "board_member",
                    "org": company,
                    "org_type": "company",
                    "member_count": len(insiders),
                    "source_url": url,
                    "evidence": (f"Both filed SEC Form 4 insider reports for "
                                 f"{company} (CIK {cik})."),
                })
                if len(results) >= _MAX_TOTAL:
                    return results
        return results

    # --- internals --------------------------------------------------------
    def _hits(self, **params) -> List[dict]:
        # EDGAR rejects an empty q on some paths; drop empty params entirely.
        params = {k: v for k, v in params.items() if v}
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", _FTS, provider=self.name, params=params,
            headers={"User-Agent": config.EDGAR_USER_AGENT},
        )
        if resp is None or resp.status_code != 200:
            return []
        try:
            return resp.json().get("hits", {}).get("hits", []) or []
        except Exception:
            return []
