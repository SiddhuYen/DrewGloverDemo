"""Conference / event speaker scraping — an event page structurally asserts its
own lineup.

An event's page asserts two things, in machine-readable schema.org JSON-LD:

  * `performer` — the people who SPOKE. Two speakers on one lineup have shared a
    stage, which is a real (if light) touch — the same class of tie as sitting
    down together on a podcast. That is `co_speaker`, tier 2.
  * `organizer` — the person/org that RAN the event. The organizer engaged every
    speaker, so they are the pivot that connects two speakers who appeared at
    DIFFERENT times: speaker #1 <-> organizer <-> speaker #2. That is
    `speaker_via_organizer`, tier 3 (a shade weaker than sharing a stage).

Rule 0: search is only ever allowed to LOCATE an event page. The JSON-LD on that
page is the structural assertion; a search snippet is not. Guard 3 (from the
firms silo) is re-applied: the searched person must actually appear in the
lineup we parsed, or the page asserts nothing about them.

Rule 1: a lineup larger than config.MAX_EVENT_SPEAKERS is a mega-conference —
its speaker set is not closeness, so no speaker<->speaker edges are materialized
(builder.materialize_org_edges enforces the same cap). The organizer pivot still
applies, because the organizer really did engage each of them.

No paid API: this is a page fetch plus a JSON-LD parse, exactly like roster
scraping. Serper locates pages when a key is present; DuckDuckGo otherwise.
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlparse

from .. import config
from ..edges.names import looks_like_person_name, normalize, person_norm_key
from . import cache
from .firms import _fetch_readable, _host, _BLOCKED_HOSTS
from .htmltext import event_roles

# Path/query words that mark a URL as plausibly an event page. Unlike a firm
# roster there is no single canonical path, so this is permissive; Guard 3 (the
# person must be in the parsed lineup) is what makes a match sound.
_EVENT_HINTS = ("event", "events", "conference", "summit", "forum", "expo",
                "speakers", "agenda", "schedule", "program", "sessions",
                "lineup", "line-up", "festival", "symposium", "meetup",
                "convention", "congress", "demo-day", "demoday")

# Never an event lineup, even when a hint appears elsewhere in the path.
_NEGATIVE = ("privacy", "terms", "careers", "jobs", "login", "signup",
             "sign-up", "cart", "checkout", "blog/tag", "wiki")


def is_event_url(url: str) -> bool:
    """True when the URL plausibly points at an event/lineup page."""
    if not url:
        return False
    host = _host(url)
    if any(bad in host for bad in _BLOCKED_HOSTS):
        return False
    path = (urlparse(url).path or "/").strip("/").lower()
    query = (urlparse(url).query or "").lower()
    hay = f"{host} {path} {query}"
    if any(neg in path for neg in _NEGATIVE):
        return False
    return any(hint in hay for hint in _EVENT_HINTS)


class EventsProvider:
    name = "events"

    def __init__(self, search_provider=None) -> None:
        self._search = search_provider

    def _available(self) -> bool:
        return self._search is not None and self._search.available()

    def _lineup_from_page(self, url: str) -> Optional[dict]:
        """Scrape one page's schema.org Event(s) into
        {event, url, start, speakers[], organizers[]}, merging every Event block
        on the page. None when the page declares no Event."""
        page = _fetch_readable(url)
        if page.status_code != 200 or not page.content:
            return None
        events = event_roles(page.content)
        if not events:
            return None

        speakers: List[str] = []
        organizers: List[str] = []
        name, start, seen_s, seen_o = "", "", set(), set()
        for ev in events:
            name = name or ev.get("name", "")
            start = start or ev.get("start", "")
            for s in ev.get("speakers", []):
                k = person_norm_key(s)
                if k and k not in seen_s and looks_like_person_name(s):
                    seen_s.add(k)
                    speakers.append(s.strip())
            for o in ev.get("organizers", []):
                # An organizer is often an ORG ("TechCrunch"); keep only NAMED
                # HUMANS as the pivot, because a person-person bridge needs a
                # person at the middle. A company organizer yields no bridge.
                k = person_norm_key(o)
                if k and k not in seen_o and looks_like_person_name(o):
                    seen_o.add(k)
                    organizers.append(o.strip())
        if not speakers:
            return None
        return {
            "event": name or _event_name_from_url(url),
            "url": url,
            "start": start,
            "speakers": speakers[: config.MAX_EVENT_SPEAKERS],
            "organizers": organizers,
            "overflow": len(speakers) > config.MAX_EVENT_SPEAKERS,
        }

    def events_for_person(self, person_name: str, max_events: int = 0,
                          hint: str = "") -> List[dict]:
        """Event lineups that LIST this person as a speaker.

        Guard 3: the person must appear in the parsed `performer` list. A page a
        search merely returned for their name asserts nothing about them.

        Returns [{event, url, start, speakers[], organizers[], overflow}].
        """
        max_events = max_events or config.MAX_EVENTS_PER_PERSON
        if not person_name or not self._available():
            return []
        target = person_norm_key(person_name)
        if not target:
            return []

        hk = target + ("|" + person_norm_key(hint) if hint and hint.strip() else "")
        key = cache.make_key(self.name, "personevents", hk)
        cached = cache.get(key)
        if cached is not None:
            return cached.get("events", [])

        h = f" {hint.strip()}" if hint and hint.strip() else ""
        candidates: List[str] = []
        for query in (f'"{person_name}"{h} conference speaker',
                      f'"{person_name}"{h} summit speakers lineup',
                      f'"{person_name}"{h} spoke at event'):
            try:
                results = self._search.search(query)
            except Exception:
                results = []
            for result in results:
                if is_event_url(result.url) and result.url not in candidates:
                    candidates.append(result.url)

        out: List[dict] = []
        seen_events = set()
        for url in candidates[: 3 * max_events]:
            lineup = self._lineup_from_page(url)
            if not lineup:
                continue
            names = {person_norm_key(s) for s in lineup["speakers"]}
            if target not in names:
                continue  # Guard 3: this lineup does not name them
            ekey = person_norm_key(lineup["event"]) or url
            if ekey in seen_events:
                continue
            seen_events.add(ekey)
            out.append(lineup)
            if len(out) >= max_events:
                break

        cache.set(key, "personevents", {"events": out}, config.CACHE_TTL)
        return out


def _event_name_from_url(url: str) -> str:
    """A readable event name from the host when the page omits one."""
    host = _host(url)
    stem = host.split(".")[0] if host else ""
    return stem.replace("-", " ").title() if stem else "event"
