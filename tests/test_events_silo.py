"""The conference / event speaker silo.

Three levels, no network:
  1. `event_roles` parses schema.org Event JSON-LD into speakers (performer) and
     the human organizer, ignoring an Organization organizer and unrelated
     Persons.
  2. `EventsProvider.events_for_person` locates a page by search, scrapes it, and
     applies Guard 3 (the searched person must be in the parsed lineup).
  3. `_from_events` writes the right edges: a tier-2 `co_speaker` clique under
     Rule 1, plus the tier-3 `speaker_via_organizer` pivot that bridges speakers
     who appeared at different times.
"""
import pytest

from app import config
from app.edges import taxonomy
from app.graph import builder
from app.graph.connect import _adjacency, _best_path
from app.graph.enrich import Enricher
from app.models import RelationshipEdge
from app.providers.base import SearchResult, Page
from app.providers.htmltext import event_roles


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """The provider cache is a shared sqlite file that survives across tests;
    disable it so each test's fake page is actually scraped, not a stale hit."""
    from app.providers import events as events_mod
    monkeypatch.setattr(events_mod.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(events_mod.cache, "set", lambda *a, **k: None)


def _p(db, name):
    return builder.get_or_create_person(db, name)


def _event_html(name, speakers, organizer=None, organizer_type="Person",
                start="2024-05-01"):
    perf = ",".join('{"@type":"Person","name":"%s"}' % s for s in speakers)
    org = (',"organizer":{"@type":"%s","name":"%s"}' % (organizer_type, organizer)
           if organizer else "")
    return ('<html><head><script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"BusinessEvent",'
            '"name":"%s","url":"https://x.test/e","startDate":"%s"%s,'
            '"performer":[%s]}</script></head><body>lineup</body></html>'
            % (name, start, org, perf))


# --- 1. the JSON-LD parser -------------------------------------------------
def test_event_roles_splits_speakers_from_organizer():
    html = _event_html("FinTech Summit", ["Sheel Mohnot", "Drew Glover"],
                       organizer="Maria Organizer")
    roles = event_roles(html)
    assert len(roles) == 1
    assert roles[0]["speakers"] == ["Sheel Mohnot", "Drew Glover"]
    assert roles[0]["organizers"] == ["Maria Organizer"]
    assert roles[0]["name"] == "FinTech Summit"


def test_event_roles_drops_an_organization_organizer():
    """A company organizer ("TechCrunch") is not a human pivot -> not returned
    among organizers, so no person-person bridge is minted through a company."""
    html = _event_html("Disrupt", ["Ada Ling", "Bo Reed"],
                       organizer="TechCrunch", organizer_type="Organization")
    roles = event_roles(html)
    assert roles[0]["speakers"] == ["Ada Ling", "Bo Reed"]
    assert roles[0]["organizers"] == []      # org organizer ignored


def test_event_roles_empty_without_event_markup():
    assert event_roles("<html><body>no json-ld</body></html>") == []
    # A page with a Person but no Event asserts no lineup.
    assert event_roles('<script type="application/ld+json">'
                       '{"@type":"Person","name":"Someone"}</script>') == []


# --- 2. the provider: locate -> scrape -> Guard 3 --------------------------
class _FakeSearch:
    def __init__(self, urls):
        self._urls = urls
    def available(self):
        return True
    def search(self, query):
        return [SearchResult("r", u, "", "fake") for u in self._urls]


def _wire_events(e, monkeypatch, urls, pages):
    """Point the events provider at fake search results and fake page fetches."""
    from app.providers import events as events_mod
    e.events._search = _FakeSearch(urls)
    monkeypatch.setattr(events_mod, "_fetch_readable",
                        lambda url: Page(status_code=200, content=pages.get(url, ""),
                                         url=url))


def test_provider_returns_a_lineup_that_names_the_person(db, monkeypatch):
    url = "https://summit.test/speakers"
    e = Enricher()
    _wire_events(e, monkeypatch, [url],
                 {url: _event_html("AI Summit", ["Drew Glover", "Sheel Mohnot"],
                                   organizer="Maria Organizer")})
    events = e.events.events_for_person("Drew Glover")
    assert len(events) == 1
    assert set(events[0]["speakers"]) == {"Drew Glover", "Sheel Mohnot"}
    assert events[0]["organizers"] == ["Maria Organizer"]


def test_provider_guard3_rejects_a_page_that_omits_the_person(db, monkeypatch):
    """A page a search returned for someone's name, but whose lineup does NOT
    list them, asserts nothing about them and is dropped."""
    url = "https://summit.test/speakers"
    e = Enricher()
    _wire_events(e, monkeypatch, [url],
                 {url: _event_html("AI Summit", ["Someone Else", "Third Party"])})
    assert e.events.events_for_person("Drew Glover") == []


def test_provider_ignores_non_event_urls(db, monkeypatch):
    url = "https://linkedin.com/in/drew"     # blocked host
    e = Enricher()
    _wire_events(e, monkeypatch, [url], {url: _event_html("X", ["Drew Glover", "A B"])})
    assert e.events.events_for_person("Drew Glover") == []


# --- 3. _from_events: the edges it writes ----------------------------------
def _run_events(db, e, monkeypatch, subject_name, url, html):
    _wire_events(e, monkeypatch, [url], {url: html})
    subject = _p(db, subject_name)
    e._hint = ""
    created = e._from_events(db, subject, None)
    db.commit()
    return created


def test_co_speaker_edges_are_tier_2(db, monkeypatch):
    e = Enricher()
    html = _event_html("DevCon", ["Drew Glover", "Sheel Mohnot", "Charles Hudson"])
    _run_events(db, e, monkeypatch, "Drew Glover", "https://devcon.test/speakers", html)

    drew, sheel = _p(db, "Drew Glover"), _p(db, "Sheel Mohnot")
    edge = _edge_between(db, drew, sheel)
    assert edge is not None
    assert edge.relationship_type == "co_speaker"
    assert edge.warmth_tier == 2               # re-tiered from 5 to podcast level


# Realistic co-speaker names for the mega-lineup (no digits, which the
# person-name validator rejects).
_MANY = ["Drew Glover", "Faraway Target", "Ada Ling", "Bo Reed", "Cara Nunez",
         "Dan Ortiz", "Eve Park", "Finn Quist", "Gina Roy", "Hal Stone",
         "Iris Tran", "Jon Ubel", "Kim Vale", "Leo Wynn", "Mia Xu"]


def test_organizer_edge_ties_every_speaker_to_the_pivot(db, monkeypatch):
    """Every speaker (incl. the subject) gets a tier-3 speaker_via_organizer edge
    to the named human organizer — the pivot the different-time bridge is built
    on."""
    e = Enricher()
    html = _event_html("Founders Forum", ["Drew Glover", "Faraway Target"],
                       organizer="Olivia Organizer")
    _run_events(db, e, monkeypatch, "Drew Glover",
                "https://forum.test/agenda", html)

    drew = _p(db, "Drew Glover")
    organizer = _p(db, "Olivia Organizer")
    target = _p(db, "Faraway Target")
    assert _edge_between(db, drew, organizer).relationship_type == "speaker_via_organizer"
    assert _edge_between(db, organizer, target).warmth_tier == 3
    # a same-lineup pair are ALSO directly co_speaker (tier 2), which pathfinding
    # rightly prefers over the 2-hop pivot — the pivot is the different-TIME case.
    assert _edge_between(db, drew, target).relationship_type == "co_speaker"


def test_mega_lineup_bridges_only_through_the_organizer(db, monkeypatch):
    """The different-time scenario: on a lineup past the Rule-1 cap, two speakers
    get NO direct co_speaker edge, so the ONLY path between them is through the
    organizer who engaged each of them — speaker <-> organizer <-> speaker."""
    monkeypatch.setattr(config, "MAX_ORG_MEMBERS_FOR_EDGES", 5)
    e = Enricher()
    html = _event_html("MegaConf", _MANY, organizer="Grace Organizer")
    _run_events(db, e, monkeypatch, "Drew Glover", "https://megaconf.test/speakers", html)

    drew = _p(db, "Drew Glover")
    other = _p(db, "Faraway Target")
    organizer = _p(db, "Grace Organizer")

    # no direct co_speaker edge in an over-cap lineup (Rule 1)
    assert _edge_between(db, drew, other) is None
    # but the organizer pivot still connects each of them
    assert _edge_between(db, drew, organizer) is not None
    assert _edge_between(db, organizer, other) is not None
    # so the ONLY route is drew -> organizer -> other
    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, other.id, config.hop_limit())
    assert [pid for pid, _e in path] == [drew.id, organizer.id, other.id]


def test_no_edges_when_the_page_asserts_no_lineup(db, monkeypatch):
    """Rule 0: no Event markup -> the provider returns nothing -> no edges."""
    e = Enricher()
    created = _run_events(db, e, monkeypatch, "Drew Glover",
                          "https://plain.test/speakers",
                          "<html><body>just prose, no json-ld</body></html>")
    assert created == 0


# --- helper ----------------------------------------------------------------
def _edge_between(db, a, b):
    lo, hi = (a.id, b.id) if a.id < b.id else (b.id, a.id)
    return db.query(RelationshipEdge).filter_by(
        person_a_id=lo, person_b_id=hi).first()
