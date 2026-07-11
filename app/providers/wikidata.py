"""Wikidata provider — structured, high-precision claims.

Every fact here is a claim someone asserted on a specific entity, which is
exactly what Rule 0 demands. We use it for three things:

  1. `is_human(qid)`     — guard against treating a company/song/election as a person.
  2. `orgs_for_person`   — the person's employers / memberships / chaired boards.
  3. `org_members`       — reverse lookup: who else holds that same claim.

`org_member_count` runs a SPARQL COUNT *before* pulling a roster, so a mega-hub
(Google, Stanford) is rejected by Rule 1 without ever fetching its members.

Deliberately EXCLUDES educated-at (P69) and political party (P102): sharing a
university or a party is not a relationship.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .. import config
from . import cache
from .base import request_with_retry
from .ratelimit import IntervalLimiter

_ENTITYDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
_WBGET = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"
_LIMITER = IntervalLimiter(config.WIKI_MIN_INTERVAL)
# Wikimedia's robot policy 403s a spoofed browser UA.
_HEADERS = {"User-Agent": config.WIKI_USER_AGENT}

# Wikidata property -> (relationship_type, evidence phrase, materialize_edges).
#
# `materialize_edges=False` means the claim records MEMBERSHIP only; it never
# becomes a person-person tie.
#
# P108 (employer) is deliberately membership-only. A shared employer is not a
# relationship — "both worked at Google" was the founding example of Rule 1 —
# and the roster-size cap cannot catch it, because `org_member_count` counts the
# humans WIKIDATA HAPPENS TO LIST, not the company's headcount. PayPal shows 24
# and Reddit 4, so both slipped under a cap of 40. Traversing P108 made a 2020s
# PayPal lawyer a "colleague" of Elon Musk, who left in 2002; the qualifiers that
# would prove non-overlap (P580/P582) are absent on almost every statement.
#
# P463 / P488 are kept: boards, academies and named bodies are genuinely small,
# Wikidata records them near-completely, and the size cap works on them (the
# American Academy of Arts and Sciences, with 9,803 members, is correctly cut).
_ORG_PROPS: Dict[str, Tuple[str, str, bool]] = {
    "P108": ("colleague", "employed at", False),
    "P463": ("board_member", "member of", True),
    "P488": ("board_member", "chairperson of", True),
}

# Family claims point DIRECTLY at another person's QID (not an org), so each is a
# pairwise assertion naming both people — no Rule 1 cap, no roster to scrape, and
# no extra network call (the entity is already fetched). This is how public and
# dynastic figures connect: Kimbal Musk -> Elon Musk, the Trump family.
_FAMILY_PROPS: Dict[str, str] = {
    "P26": "is married to",
    "P3373": "is a sibling of",
    "P22": "is the child of",          # subject's father
    "P25": "is the child of",          # subject's mother
    "P40": "is a parent of",           # subject's child
    "P1038": "is a relative of",
}

# --- what a P463 "member of" target must BE to imply a personal tie ---------
#
# "Member of" points at real bodies (a board, an academy, a learned society) but
# also at things that are not bodies at all. Verified on live data:
#
#     The World's Billionaires -> instance of: order              (a Forbes LIST)
#     SAG-AFTRA                -> instance of: political coalition (a UNION)
#
# Materialising either as `board_member` would assert that Trump, Musk and
# Buffett "served on the same board". Sharing a rich-list is not a relationship,
# any more than sharing an employer is.
#
# A deny term always wins; otherwise the target must look like an organisation.
# Unknown kinds FAIL CLOSED — P463 is low-volume, so conservatism costs little,
# while a single bad kind fabricates a clique.
_ORG_KIND_DENY = (
    "order", "award", "prize", "medal", "honorary", "title", "list",
    "coalition", "political party", "trade union", "union", "disambiguation",
    "category", "ranking", "class of", "occupation",
)
_ORG_KIND_ALLOW = (
    "organization", "organisation", "society", "academy", "association",
    "institute", "institution", "company", "enterprise", "business",
    "corporation", "board", "committee", "council", "foundation", "university",
    "college", "school", "agency", "firm", "laboratory",
)

_MAX_ORGS = 6
_MAX_FAMILY = 8
# Hard ceiling on a SPARQL roster pull. Anything near this is a mega-hub and
# will be rejected by Rule 1 anyway; the +1 lets the caller see it overflowed.
_ROSTER_LIMIT = config.MAX_ORG_MEMBERS_FOR_EDGES + 1


class WikidataProvider:
    name = "wikidata"

    # --- identity ---------------------------------------------------------
    def is_human(self, qid: str) -> bool:
        """True iff the QID is instance-of human (P31=Q5)."""
        return bool(qid) and _is_human(self._entity_claims(qid))

    # --- person -> orgs ---------------------------------------------------
    def orgs_for_person(self, qid: str) -> List[dict]:
        """[{org_qid, org_name, prop, relationship_type, phrase}] for a person."""
        if not qid:
            return []
        key = cache.make_key(self.name, "orgs", qid)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("orgs", [])

        claims = self._entity_claims(qid)
        pairs: List[Tuple[str, str]] = []
        for prop in _ORG_PROPS:
            for stmt in claims.get(prop, []) or []:
                tgt = _claim_target_qid(stmt)
                if tgt:
                    pairs.append((prop, tgt))
        pairs = pairs[:_MAX_ORGS]

        labels = self._labels([t for _p, t in pairs])
        out: List[dict] = []
        for prop, tgt in pairs:
            rtype, phrase, materialize = _ORG_PROPS[prop]
            name = labels.get(tgt)
            if name:
                out.append({"org_qid": tgt, "org_name": name, "prop": prop,
                            "relationship_type": rtype, "phrase": phrase,
                            "materialize_edges": materialize})
        cache.set(key, "orgs", {"orgs": out}, config.CACHE_TTL_WIKI)
        return out

    def family_for_person(self, qid: str) -> List[dict]:
        """[{person_qid, person_name, phrase}] — this person's family members.

        Each family claim's target IS another person's QID, so the assertion
        names both people directly (unlike an org claim). No Rule 1 cap applies.
        """
        if not qid:
            return []
        key = cache.make_key(self.name, "family", qid)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("family", [])

        claims = self._entity_claims(qid)
        pairs: List[Tuple[str, str]] = []
        seen = set()
        for prop in _FAMILY_PROPS:
            for stmt in claims.get(prop, []) or []:
                tgt = _claim_target_qid(stmt)
                if tgt and tgt not in seen:
                    seen.add(tgt)
                    pairs.append((prop, tgt))
        pairs = pairs[:_MAX_FAMILY]

        labels = self._labels([t for _p, t in pairs])
        out: List[dict] = []
        for prop, tgt in pairs:
            name = labels.get(tgt)
            if name and not _looks_like_qid(name):
                out.append({"person_qid": tgt, "person_name": name,
                            "phrase": _FAMILY_PROPS[prop]})
        cache.set(key, "family", {"family": out}, config.CACHE_TTL_WIKI)
        return out

    # --- what kind of thing is this org? ----------------------------------
    def org_kinds(self, org_qid: str) -> List[str]:
        """English labels of the org's `instance of` (P31) claims. Cached."""
        if not org_qid:
            return []
        key = cache.make_key(self.name, "kinds", org_qid)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("kinds", [])

        claims = self._entity_claims(org_qid)   # keeps P31; reuses the entity cache
        kind_qids = [q for q in (_claim_target_qid(s) for s in claims.get("P31", []))
                     if q]
        labels = self._labels(kind_qids)
        kinds = [labels[q].lower() for q in kind_qids if q in labels]
        cache.set(key, "kinds", {"kinds": kinds}, config.CACHE_TTL_WIKI)
        return kinds

    def org_is_board_like(self, org_qid: str) -> bool:
        """True when a `member of` target is a body whose membership implies a
        personal tie. A deny term wins; unknown kinds fail closed."""
        kinds = self.org_kinds(org_qid)
        if not kinds:
            return False
        for kind in kinds:
            if any(bad in kind for bad in _ORG_KIND_DENY):
                return False
        return any(good in kind
                   for kind in kinds for good in _ORG_KIND_ALLOW)

    # --- org -> members (Rule 1 aware) ------------------------------------
    def org_member_count(self, org_qid: str, prop: str) -> int:
        """How many humans hold `prop -> org_qid`. Cheap COUNT, so a mega-hub is
        rejected by Rule 1 before we ever fetch its roster."""
        key = cache.make_key(self.name, f"count:{prop}", org_qid)
        cached = cache.get(key)
        if cached is not None:
            return int(cached.get("n", 0))
        query = (f"SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE {{ "
                 f"?p wdt:{prop} wd:{org_qid} . ?p wdt:P31 wd:Q5 . }}")
        n = 0
        rows = self._sparql(query, ["n"])
        if rows:
            try:
                n = int(rows[0]["n"])
            except (ValueError, KeyError):
                n = 0
        cache.set(key, "count", {"n": n}, config.CACHE_TTL_WIKI)
        return n

    def org_members(self, org_qid: str, prop: str,
                    limit: int = _ROSTER_LIMIT) -> List[str]:
        """Names of humans holding `prop -> org_qid`."""
        key = cache.make_key(self.name, f"members:{prop}:{limit}", org_qid)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("members", [])
        query = (
            f"SELECT ?pLabel WHERE {{ ?p wdt:{prop} wd:{org_qid} . "
            f"?p wdt:P31 wd:Q5 . SERVICE wikibase:label "
            f"{{ bd:serviceParam wikibase:language 'en'. }} }} LIMIT {limit}"
        )
        rows = self._sparql(query, ["pLabel"])
        # An unlabeled entity falls back to its Q-id; those are not usable names.
        members = [r["pLabel"] for r in rows
                   if r.get("pLabel") and not _looks_like_qid(r["pLabel"])]
        cache.set(key, "members", {"members": members}, config.CACHE_TTL_WIKI)
        return members

    # --- internals --------------------------------------------------------
    def _sparql(self, query: str, fields: List[str]) -> List[Dict[str, str]]:
        _LIMITER.acquire()
        resp = request_with_retry(
            "GET", _SPARQL, provider=self.name,
            params={"query": query, "format": "json"},
            headers={**_HEADERS, "Accept": "application/sparql-results+json"},
        )
        if resp is None or resp.status_code != 200:
            return []
        try:
            rows = resp.json().get("results", {}).get("bindings", [])
        except Exception:
            return []
        out = []
        for r in rows:
            row = {f: r[f]["value"] for f in fields if f in r}
            if row:
                out.append(row)
        return out

    def _entity_claims(self, qid: str) -> Dict[str, list]:
        key = cache.make_key(self.name, "claims", qid)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("claims", {})
        _LIMITER.acquire()
        resp = request_with_retry("GET", _ENTITYDATA.format(qid=qid),
                                  provider=self.name, headers=_HEADERS)
        if resp is None or resp.status_code != 200:
            return {}  # transport failure: don't cache it as "no claims"
        claims: Dict[str, list] = {}
        try:
            entity = resp.json().get("entities", {}).get(qid, {})
            claims = entity.get("claims", {}) or {}
        except Exception:
            return {}
        # Keep only the properties we read, so the cache row stays small.
        keep = set(_ORG_PROPS) | set(_FAMILY_PROPS) | {"P31"}
        claims = {k: v for k, v in claims.items() if k in keep}
        cache.set(key, "claims", {"claims": claims}, config.CACHE_TTL_WIKI)
        return claims

    def _labels(self, qids: List[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        uniq = list(dict.fromkeys(qids))
        for i in range(0, len(uniq), 50):
            batch = uniq[i:i + 50]
            _LIMITER.acquire()
            resp = request_with_retry(
                "GET", _WBGET, provider=self.name, headers=_HEADERS,
                params={"action": "wbgetentities", "ids": "|".join(batch),
                        "props": "labels", "languages": "en", "format": "json"},
            )
            if resp is None or resp.status_code != 200:
                continue
            try:
                for q, ent in resp.json().get("entities", {}).items():
                    label = ent.get("labels", {}).get("en", {}).get("value")
                    if label:
                        out[q] = label
            except Exception:
                continue
        return out


def _looks_like_qid(s: str) -> bool:
    return len(s) > 1 and s[0] == "Q" and s[1:].isdigit()


def _is_human(claims: dict) -> bool:
    return any(_claim_target_qid(s) == "Q5" for s in (claims.get("P31") or []))


def _claim_target_qid(stmt: dict) -> Optional[str]:
    try:
        dv = stmt["mainsnak"]["datavalue"]["value"]
        if isinstance(dv, dict) and dv.get("entity-type") == "item":
            return dv.get("id")
    except Exception:
        return None
    return None
