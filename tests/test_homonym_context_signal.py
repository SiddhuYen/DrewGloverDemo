"""Fix: the homonym guard's own evidence could already be about the wrong
(more famous) person.

_web_background did one generic web search on the name (plus whatever hint was
given) and trusted whatever came back as "who we're actually looking for." When
no hint is given and the searched name is also held by someone more famous, an
ordinary web search is dominated by the famous person's coverage — so the
"signal" meant to represent the real target can already describe the wrong
person, and the guard would confirm a match against contaminated evidence
instead of catching it.

Two changes: (1) when a hint IS given, _web_background now filters results down
to the ones that actually mention it, instead of just folding the hint into the
query string and hoping the search engine weights it enough; (2) when NO hint
is given at all, that's now surfaced as identity_needs_context so a caller can
proactively add one, rather than only finding out after a possibly-wrong guess.
"""
from app import config
from app.graph import builder
import app.graph.enrich as enrich_mod
from app.graph.connect import _homonym_needs_context
from app.graph.enrich import Enricher
from app.providers.base import SearchResult


def _person(db, name):
    return builder.get_or_create_person(db, name)


def _result(title, snippet):
    return SearchResult(title, "https://example.com", snippet, "test")


# --- _web_background filters toward the hint --------------------------------
def test_background_filters_out_results_that_ignore_the_hint(monkeypatch):
    """A famous-namesake-dominated result set: only two of the five snippets
    are actually about the tutoring-company founder we asked about."""
    results = [
        _result("Jamie Fox — celebrity gossip", "the actor was seen at a premiere"),
        _result("Jamie Fox, Pantheon Prep", "co-founder of a test-prep tutoring startup"),
        _result("Jamie Fox filmography", "known for his award-winning roles"),
        _result("Pantheon Prep admissions", "Jamie Fox's tutoring company opens a new office"),
        _result("Jamie Fox interview", "the actor discusses his new film"),
    ]

    class _FakeProvider:
        def search(self, query):
            return results

    e = Enricher()
    monkeypatch.setattr(enrich_mod, "_search_provider", lambda: _FakeProvider())

    background = e._web_background("Jamie Fox", "tutoring test-prep")

    assert "Pantheon Prep" in background
    assert "premiere" not in background
    assert "filmography" not in background
    assert "award-winning" not in background


def test_background_falls_back_to_unfiltered_when_the_hint_matches_nothing(monkeypatch):
    """A hint that's a paraphrase (or a name the snippets never spell out
    literally) must not zero out the background entirely — degrade to the old
    unfiltered behaviour rather than returning nothing to compare against."""
    results = [_result("Jane Doe", "does something entirely unrelated to the hint")]

    class _FakeProvider:
        def search(self, query):
            return results

    e = Enricher()
    monkeypatch.setattr(enrich_mod, "_search_provider", lambda: _FakeProvider())

    background = e._web_background("Jane Doe", "a phrase matching nothing")

    assert "unrelated to the hint" in background


def test_background_with_no_hint_is_unfiltered(monkeypatch):
    results = [_result("Someone", "a plain snippet with no hint to filter by")]

    class _FakeProvider:
        def search(self, query):
            return results

    e = Enricher()
    monkeypatch.setattr(enrich_mod, "_search_provider", lambda: _FakeProvider())

    background = e._web_background("Someone", "")

    assert "a plain snippet" in background


# --- needs_context: surfaced when the check ran with no hint at all ---------
def test_needs_context_is_set_when_a_candidate_exists_and_hint_is_empty(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "some background found by an unguided search"
    e._hint = ""
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    e.wikidata.orgs_for_person = lambda qid: []
    subj = _person(db, "Common Name")

    e._identity_confirmed(subj, "Q999", None)

    assert e._identity_needs_context == {"qid": "Q999",
                                         "description": "American venture capitalist"}


def test_needs_context_is_not_set_when_a_hint_was_given(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "partner at a seed-stage venture capital fund"
    e._hint = "venture capital"
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    e.wikidata.orgs_for_person = lambda qid: []
    subj = _person(db, "Jane Investor")

    e._identity_confirmed(subj, "Q999", None)

    assert e._identity_needs_context is None


def test_needs_context_is_surfaced_through_connect(db):
    from app.graph import connect as connect_mod

    drew = _person(db, "Drew Glover")
    target = _person(db, "Common Name")
    builder.add_edge(db, drew, target, "cofounder")
    target.meta = {"homonym_needs_context": {"qid": "Q999",
                                             "description": "American venture capitalist"}}
    db.commit()

    out = connect_mod.connect_people(db, "Drew Glover", "Common Name")

    assert out["identity_needs_context"] == {"qid": "Q999",
                                             "description": "American venture capitalist"}


def test_needs_context_is_none_without_the_meta_flag(db):
    subj = _person(db, "Ordinary Person")
    assert _homonym_needs_context(subj) is None
