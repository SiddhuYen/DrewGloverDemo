"""Firm roster scraping — a team page structurally asserts its own roster.

Search is only ever allowed to LOCATE a page. The roster on that page is the
structural assertion. Nothing here mints an edge from a search snippet.

Three guards, each of which cost a real bug:

  1. A page must LOOK like a roster (`/team`, `/people`, ...) or a portfolio
     index. A firm's homepage is neither: Fiat's interleaves its three partners
     with quoted portfolio founders, and NER cannot tell them apart.
  2. A page must BELONG to the firm, established by IDENTITY rather than keyword
     presence. "Homebrew team page" returns the package manager's `brew.sh` cask
     index; "Storm Ventures portfolio" returns CalmStorm's companies, whose page
     reads "Calm Storm Ventures" and so contains the words "storm ventures".
  3. For person->firm resolution, the PERSON must appear on the roster. A page
     merely returned by a search for someone's name asserts nothing about them.

Portfolio pages get a fourth property: a company's IDENTITY is the domain the
page links out to, never its printed name. Two firms both linking `airship.com`
back the same company however each spells it — and "Bolt" the scooter company
and "Bolt" the checkout company must never merge.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set
from urllib.parse import urlparse

from .. import config, extract
from ..edges.names import normalize, org_norm_key, person_norm_key
from . import cache
from .base import fetch_page
from .htmltext import soup_of, text_blocks

# Path segments that mark a page as a roster of people.
#
# Deliberately EXCLUDES "/about" and "/founders". An about page interleaves the
# team with portfolio companies: Slow Ventures' listed "Human Interest",
# "Domino Data Lab" and "Good Dog" as people — all proper nouns, and NER types
# none of them as an organization. Losing Slow Ventures is the right trade.
_ROSTER_HINTS = ("team", "people", "our-team", "ourteam", "partners", "staff",
                 "leadership", "who-we-are", "whoweare", "members",
                 "our-firm", "ourfirm", "crew", "humans")

# Never a roster, even when a hint appears elsewhere in the path.
_NEGATIVE_HINTS = ("portfolio", "blog", "post", "news", "careers", "jobs",
                   "contact", "privacy", "terms", "press", "insights",
                   "cask", "formula", "wiki", "forum", "comments")

# Aggregators and socials: real pages, but never the firm's own roster.
_BLOCKED_HOSTS = ("linkedin.com", "twitter.com", "x.com", "facebook.com",
                  "crunchbase.com", "pitchbook.com", "dealroom.co", "f6s.com",
                  "reddit.com", "wikipedia.org", "medium.com", "youtube.com",
                  "signal.nfx.com", "app.dealroom.co")

# Tokens too generic to identify a firm on a page.
_GENERIC_FIRM_TOKENS = {"ventures", "capital", "partners", "fund", "funds",
                        "group", "management", "the", "and", "vc", "llc", "lp"}


def _host(url: str) -> str:
    # NB: not .lstrip("www."), which strips any leading 'w'/'.' characters and
    # would turn "wework.com" into "ework.com".
    host = (urlparse(url).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_roster_url(url: str) -> bool:
    """True when the URL path looks like a team/people page (not a homepage)."""
    if not url:
        return False
    host = _host(url)
    if any(bad in host for bad in _BLOCKED_HOSTS):
        return False
    path = (urlparse(url).path or "/").strip("/").lower()
    if not path:
        return False  # bare homepage
    if any(neg in path for neg in _NEGATIVE_HINTS):
        return False
    return any(hint in path.split("/") or hint in path for hint in _ROSTER_HINTS)


_PORTFOLIO_HINTS = ("portfolio", "companies", "investments")


def is_portfolio_url(url: str) -> bool:
    """True when the URL path is a firm's portfolio index (not a homepage)."""
    if not url:
        return False
    if any(bad in _host(url) for bad in _BLOCKED_HOSTS):
        return False
    path = (urlparse(url).path or "/").strip("/").lower()
    if not path:
        return False
    segments = path.split("/")
    # The index ends at the hint. "/portfolio/acme" is one company, not the list.
    return any(hint in segments[-1] for hint in _PORTFOLIO_HINTS)


def _company_name_from_anchor(anchor) -> str:
    """The company name inside a portfolio anchor.

    Prefer the logo's `alt` — Foundry's anchors carry alt="Airship" while their
    link text is "Portland, OR". Reject location-shaped and empty text.
    """
    from ..edges.names import is_noise_name

    image = anchor.find("img")
    alt = (image.get("alt") or "").strip() if image else ""
    text = anchor.get_text(" ", strip=True)

    for candidate in (alt, text):
        candidate = " ".join(candidate.split())
        if not candidate or len(candidate) > 48:
            continue
        # "San Francisco, CA" is where the company is, not who it is.
        if "," in candidate or is_noise_name(candidate):
            continue
        return candidate
    return ""


def firm_tokens(firm_name: str) -> Set[str]:
    """Distinctive words of a firm name ("Uncork Capital" -> {"uncork"})."""
    return {t for t in normalize(firm_name).split()
            if t and t not in _GENERIC_FIRM_TOKENS and len(t) > 2}


def page_belongs_to_firm(url: str, html: str, firm_name: str) -> bool:
    """Guard 2. The page must BE this firm's, established by identity — the
    domain, or the name the page declares for itself.

    Keyword presence is not identity, and every weaker test failed on real data:

      * substring on the domain — "calmstorm.vc" contains "storm", so a search
        for "Storm Ventures portfolio" returns a rival firm's companies;
      * "Homebrew team page" returns the package manager's `brew.sh` cask index;
      * the firm's full name on a word boundary in the page text — CalmStorm's
        page reads "Calm Storm Ventures", which contains "storm ventures".

    So: the domain must begin with a distinctive token of the firm's name, or
    the page's own declared name must equal that firm (allowing an initialism,
    since "btv.vc" declares itself "BTV" and means Better Tomorrow Ventures).
    """
    tokens = firm_tokens(firm_name)
    if not tokens:
        return True  # nothing distinctive to check against; fall back to Guard 1

    stem = _domain_stem(url)
    domain_hit = bool(stem) and any(stem.startswith(tok) or tok.startswith(stem)
                                    for tok in tokens)
    # A bare domain match settles a single-token firm ("Homebrew" -> homebrew.co).
    # It does NOT settle a multi-word one: "Invesco Private Capital" matched
    # invesco.com/.../invesco-private-CREDIT/team.html, and we attached a
    # different business unit's 28 staff to the VC arm that made the investment.
    if domain_hit and len(tokens) == 1:
        return True

    declared = firm_name_from_page(html, url)
    if not declared:
        return False
    if org_norm_key(declared) == org_norm_key(firm_name):
        return True
    initials = "".join(word[0] for word in normalize(firm_name).split() if word)
    return normalize(declared).replace(" ", "") == initials


def _page_title(html: str) -> str:
    title = soup_of(html).title
    return title.get_text(" ", strip=True) if title else ""


_TLD_TOKENS = {"co", "com", "vc", "io", "ai", "net", "org", "fund", "capital"}


def _domain_stem(url: str) -> str:
    """"www.hustlefund.vc" -> "hustlefund"; "btv.vc" -> "btv"."""
    host = _host(url)
    parts = [p for p in host.split(".") if p]
    return re.sub(r"[^a-z0-9]", "", parts[0]) if parts else ""


def firm_name_from_page(html: str, url: str) -> str:
    """Display name of the firm behind a roster page, VERIFIED by the domain.

    A <title> is "BTV | Sheel Mohnot" or "A team of good humans... | Hustle
    Fund". Taking the longest segment yields a person's name or a tagline, and
    naming an organization after a person corrupts the graph. So we accept only
    the title segment whose letters match the registrable domain:

        stormventures.com  <- "Storm Ventures"   accept
        btv.vc             <- "BTV"              accept
        btv.vc             <- "Sheel Mohnot"     reject

    Falls back to the domain stem, which is always at least honest.
    """
    from ..edges.names import looks_like_person_name

    stem = _domain_stem(url)
    title = _page_title(html)
    for segment in re.split(r"[|\-–—:·]", title):
        segment = segment.strip()
        if not segment or looks_like_person_name(segment):
            continue
        tokens = [t for t in normalize(segment).split() if t]
        while tokens and tokens[-1] in _TLD_TOKENS and len(tokens) > 1:
            tokens.pop()          # "Homebrew.co" -> "Homebrew"
        key = "".join(tokens)
        if key and stem and (key == stem or key in stem or stem in key):
            # "Homebrew.co" -> "Homebrew": drop a trailing TLD from the surface
            # form too, not just from the comparison key.
            display = re.sub(r"\.(co|com|vc|io|ai|net|org)$", "", segment,
                             flags=re.I).strip(" .")
            return display or segment.strip()
    return stem.title() if stem else ""


class FirmsProvider:
    """Locate and scrape firm team pages, and resolve a person to their firm.

    `search_provider` exposes .search(query) -> [SearchResult] and .available().
    """

    name = "firms"

    def __init__(self, search_provider=None) -> None:
        self._search = search_provider

    def _available(self) -> bool:
        return self._search is not None and self._search.available()

    # --- firm -> roster ---------------------------------------------------
    def find_team_page(self, firm_name: str) -> Optional[str]:
        """The firm's own roster URL, or None. Verified by Guard 2."""
        if not firm_name or not self._available():
            return None
        key = cache.make_key(self.name, "teampage", org_norm_key(firm_name))
        cached = cache.get(key)
        if cached is not None:
            return cached.get("url") or None

        candidates: List[str] = []
        for query in (f"{firm_name} team page",
                      f"{firm_name} our team partners",
                      f'"{firm_name}" about the team'):
            for result in self._search.search(query):
                if is_roster_url(result.url) and result.url not in candidates:
                    candidates.append(result.url)
            if candidates:
                break

        # Prefer the shallowest path: "/team" is the roster, "/team/andy" is one
        # person's bio page.
        candidates.sort(key=lambda u: len(urlparse(u).path.strip("/").split("/")))

        url = ""
        for candidate in candidates:
            page = fetch_page(candidate)
            if page.status_code == 200 and page.content and \
                    page_belongs_to_firm(candidate, page.content, firm_name):
                url = candidate
                break
        cache.set(key, "teampage", {"url": url}, config.CACHE_TTL)
        return url or None

    def roster(self, url: str, firm_name: str = "") -> dict:
        """Scrape a roster page. Returns {firm, url, members[], overflow}.

        `overflow` is True when the page lists more people than Rule 1 permits,
        so the caller records membership without materializing a false clique.
        """
        out = {"firm": firm_name, "url": url, "members": [], "overflow": False}
        if not is_roster_url(url):
            return out  # Guard 1: a non-roster page asserts no roster

        key = cache.make_key(self.name, "roster", f"{org_norm_key(firm_name)}::{url}")
        cached = cache.get(key)
        if cached is not None:
            return cached

        page = fetch_page(url)
        if page.status_code != 200 or not page.content:
            return out
        if firm_name and not page_belongs_to_firm(url, page.content, firm_name):
            return out  # Guard 2: this page is not this firm's

        # Per-element blocks, never one flattened string: a roster puts each
        # name in its own cell, and flattening glues neighbouring "Email" /
        # "Linkedin" labels onto it.
        blocks = text_blocks(page.content)
        if not blocks:
            return out  # a JS-rendered shell asserts nothing we can read

        # Deterministic name shape first, then grammar (POS accepts, NER vetoes).
        from ..graph.builder import clean_person_names
        names = extract.filter_person_blocks(clean_person_names(blocks))

        out["firm"] = firm_name or firm_name_from_page(page.content, url)
        out["members"] = names[: config.MAX_ROSTER_MEMBERS]
        out["overflow"] = len(names) > config.MAX_ORG_MEMBERS_FOR_EDGES
        cache.set(key, "roster", out, config.CACHE_TTL)
        return out

    def roster_for_firm(self, firm_name: str) -> dict:
        url = self.find_team_page(firm_name)
        if not url:
            return {"firm": firm_name, "url": "", "members": [], "overflow": False}
        result = self.roster(url, firm_name)
        result["firm"] = firm_name or result.get("firm", "")
        return result

    # --- firm -> portfolio -------------------------------------------------
    def find_portfolio_page(self, firm_name: str) -> Optional[str]:
        """The firm's own portfolio URL, or None. Verified by Guard 2."""
        if not firm_name or not self._available():
            return None
        key = cache.make_key(self.name, "portfoliopage", org_norm_key(firm_name))
        cached = cache.get(key)
        if cached is not None:
            return cached.get("url") or None

        candidates: List[str] = []
        for query in (f"{firm_name} portfolio companies",
                      f"{firm_name} investments"):
            for result in self._search.search(query):
                if is_portfolio_url(result.url) and result.url not in candidates:
                    candidates.append(result.url)
            if candidates:
                break
        candidates.sort(key=lambda u: len(urlparse(u).path.strip("/").split("/")))

        url = ""
        for candidate in candidates:
            page = fetch_page(candidate)
            if page.status_code == 200 and page.content and \
                    page_belongs_to_firm(candidate, page.content, firm_name):
                url = candidate
                break
        cache.set(key, "portfoliopage", {"url": url}, config.CACHE_TTL)
        return url or None

    def portfolio(self, url: str, firm_name: str = "") -> dict:
        """Companies a firm's portfolio page links out to.

        The structural signal is the OUTBOUND LINK: a portfolio page links each
        company to its own website, and the company's name sits in the anchor's
        image `alt`. The href's domain is the identity — two firms that both
        link `airship.com` back the same company however each spells the name.

        Returns {firm, url, companies: [{name, domain}]}.
        """
        out = {"firm": firm_name, "url": url, "companies": []}
        if not is_portfolio_url(url):
            return out

        key = cache.make_key(self.name, "portfolio",
                             f"{org_norm_key(firm_name)}::{url}")
        cached = cache.get(key)
        if cached is not None:
            return cached

        page = fetch_page(url)
        if page.status_code != 200 or not page.content:
            return out
        if firm_name and not page_belongs_to_firm(url, page.content, firm_name):
            return out

        own_host = _host(url)
        seen, companies = set(), []
        for anchor in soup_of(page.content).find_all("a", href=True):
            href = anchor["href"]
            if not href.startswith("http"):
                continue
            domain = _host(href)
            if not domain or domain in seen:
                continue
            # The firm's own site, including subdomains: "docs.wing.vc" is Wing's
            # documentation, not a company Wing backs.
            if (domain == own_host or domain.endswith("." + own_host)
                    or own_host.endswith("." + domain)):
                continue
            if any(bad in domain for bad in _BLOCKED_HOSTS):
                continue

            name = _company_name_from_anchor(anchor)
            if not name:
                continue
            seen.add(domain)
            companies.append({"name": name, "domain": domain})
            if len(companies) >= config.MAX_PORTFOLIO_COMPANIES:
                break

        out["companies"] = companies
        cache.set(key, "portfolio", out, config.CACHE_TTL)
        return out

    def portfolio_for_firm(self, firm_name: str) -> dict:
        url = self.find_portfolio_page(firm_name)
        if not url:
            return {"firm": firm_name, "url": "", "companies": []}
        result = self.portfolio(url, firm_name)
        result["firm"] = firm_name
        return result

    # --- person -> firm ---------------------------------------------------
    def find_person_firms(self, person_name: str, max_firms: int = 0) -> List[dict]:
        """Roster pages that LIST this person, with the firm they belong to.

        Guard 3: the person's name must appear on the roster we scraped. A page
        that a search merely returned for their name asserts nothing about them —
        that is the co-occurrence fallacy wearing a URL.

        Returns [{firm, url, members[], overflow}], at most `max_firms`.
        """
        max_firms = max_firms or config.MAX_FIRMS_PER_PERSON
        if not person_name or not self._available():
            return []

        target = person_norm_key(person_name)
        if not target:
            return []
        key = cache.make_key(self.name, "personfirms", target)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("firms", [])

        candidates: List[str] = []
        for query in (f'"{person_name}" venture capital team',
                      f'"{person_name}" partner venture firm team page',
                      f'"{person_name}" team'):
            for result in self._search.search(query):
                if is_roster_url(result.url) and result.url not in candidates:
                    candidates.append(result.url)
        # "/team" is the roster; "/team-members/sheel-mohnot" is one bio page.
        candidates.sort(key=lambda u: len(urlparse(u).path.strip("/").split("/")))

        # Keep the FULLEST verified roster per firm. A bio page and the roster
        # both name the person and share a domain, but the bio page lists three
        # colleagues where the roster lists nineteen.
        best: dict = {}
        for url in candidates[: 3 * max_firms]:
            # No firm name to verify against here, so Guard 2 is skipped and the
            # firm is derived from the page (domain-verified). Guard 3 below is
            # what makes this sound.
            roster = self.roster(url)
            members = roster.get("members") or []
            if target not in {person_norm_key(m) for m in members}:
                continue  # Guard 3: this roster does not name them

            page = fetch_page(url)
            firm = firm_name_from_page(page.content, url)
            if not firm:
                continue
            roster["firm"] = firm

            # Hand the firm's NAME back to firm->roster discovery. A search for a
            # person surfaces their bio page ("/team-members/tae-hea-nahm", 3
            # colleagues) far more often than the firm's roster ("/our-team", 19).
            # Guard 3 is re-applied: the fuller page must still name them.
            full = self.roster_for_firm(firm)
            full_members = full.get("members") or []
            if (len(full_members) > len(members)
                    and target in {person_norm_key(m) for m in full_members}):
                roster = full
                roster["firm"] = firm

            firm_key = org_norm_key(firm)
            if len(roster["members"]) > len(best.get(firm_key, {}).get("members") or []):
                best[firm_key] = roster

        found = sorted(best.values(), key=lambda r: -len(r["members"]))[:max_firms]
        cache.set(key, "personfirms", {"firms": found}, config.CACHE_TTL)
        return found
