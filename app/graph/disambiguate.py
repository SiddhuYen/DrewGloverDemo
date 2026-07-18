"""Deterministic homonym disambiguation backstop.

When no Claude key is present to judge identity semantically, decide whether two
short descriptions of the same NAME plainly point at different professional
worlds — a venture capitalist vs. a test-prep educator — so a non-notable
searched person is never fused with a notable stranger who happens to share the
name.

Conservative by design: it reports a conflict ONLY when each side clearly and
separately anchors in a different domain, and stays silent (no conflict)
whenever the two overlap or either side is unclear. The gate that calls it can
only SEPARATE nodes, never merge them, so a false negative merely preserves the
prior behavior and a false positive costs at most a few Wikidata edges for one
person — never a wrong bridge.
"""
from __future__ import annotations

import re

# Professional-domain lexicon. Each cluster is a set of lowercase keywords that,
# appearing in a short bio/description, anchor a person in that world. Kept
# deliberately generic — this is a backstop, not an ontology.
_DOMAINS = {
    "venture": {"venture", "vc", "investor", "investing", "investment",
                "capital", "fund", "general partner", "limited partner",
                "angel investor", "portfolio", "financier"},
    "education": {"education", "educator", "teacher", "tutor", "tutoring",
                  "test prep", "prep", "admissions", "academy", "curriculum",
                  "edtech", "professor", "lecturer", "principal of"},
    "sports": {"footballer", "cricketer", "athlete", "coach", "olympic",
               "boxer", "wrestler", "sprinter", "basketball", "baseball",
               "quarterback", "midfielder", "batsman", "bowler"},
    "music": {"singer", "musician", "composer", "rapper", "songwriter",
              "guitarist", "drummer", "pianist", "vocalist", "band"},
    "film": {"actor", "actress", "filmmaker", "screenwriter", "comedian",
             "cinematographer", "voice actor"},
    "politics": {"politician", "senator", "governor", "minister", "congressman",
                 "congresswoman", "mayor", "diplomat", "legislator", "councillor"},
    "science": {"scientist", "researcher", "physicist", "chemist", "biologist",
                "mathematician", "astronomer", "academic", "engineer"},
    "medicine": {"physician", "surgeon", "cardiologist", "psychiatrist",
                 "dentist", "doctor of medicine"},
    "law": {"lawyer", "attorney", "barrister", "solicitor", "jurist", "judge"},
    "military": {"general", "colonel", "brigadier", "admiral", "soldier",
                 "army officer", "naval officer", "air force"},
    "religion": {"priest", "pastor", "imam", "rabbi", "monk", "bishop",
                 "cleric", "theologian"},
    "arts": {"author", "novelist", "poet", "painter", "sculptor", "journalist",
             "cartoonist", "playwright"},
}

# Compile one boundary-anchored pattern per domain. `\b` bounds even the short
# tokens (vc, mp) and lets multi-word keywords ("test prep") match as phrases.
_DOMAIN_RES = {
    domain: re.compile(
        r"\b(?:%s)\b" % "|".join(re.escape(kw) for kw in sorted(kws)),
        re.IGNORECASE,
    )
    for domain, kws in _DOMAINS.items()
}


def domains_of(text: str) -> set:
    """The professional domains a short description anchors in (possibly empty)."""
    if not text:
        return set()
    return {d for d, rx in _DOMAIN_RES.items() if rx.search(text)}


def domain_conflict(signal: str, candidate: str) -> bool:
    """True when `signal` (who we are actually looking for — web background plus
    the user's context) and `candidate` (a Wikidata entry for the same name)
    anchor in different, non-overlapping professional domains.

    Silent (False) when either side is unanchored or the two share any domain,
    so it only fires on a clear cross-domain mismatch.
    """
    s = domains_of(signal)
    c = domains_of(candidate)
    if not s or not c:
        return False
    return s.isdisjoint(c)
