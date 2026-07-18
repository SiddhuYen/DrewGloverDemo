"""Fix: the homonym guard could not tell apart two same-named people IN the
same broad field.

_identity_confirmed compared a career CATEGORY ("venture capitalist") against
another career category — which is exactly equal for two different VCs who
happen to share a name, so the LLM-judged path had nothing to disambiguate on
beyond two matching adjectives. The deterministic domain-conflict backstop is
even coarser (it only fires on fully DISJOINT categories), so it was never
going to catch this either — that ceiling is inherent to a keyword-bucket
check and out of scope here.

The fix feeds the candidate's specific, known Wikidata affiliations (from
orgs_for_person) into the signal compared against — a concrete "affiliated
with Different Capital" is something an LLM can actually weigh against a
background that says "partner at Acme Capital", where "venture capitalist"
vs "venture capitalist" could not.
"""
from app import config
from app.graph import builder
import app.graph.enrich as enrich_mod
from app.graph.enrich import Enricher


def _person(db, name):
    return builder.get_or_create_person(db, name)


def test_identity_check_includes_known_orgs_in_the_candidate_signal(db, monkeypatch):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "partner at Acme Capital, a seed-stage fund"
    e._hint = ""
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    e.wikidata.orgs_for_person = lambda qid: [
        {"org_name": "Different Capital", "org_qid": "Q1", "prop": "P108"},
        {"org_name": "Another Fund", "org_qid": "Q2", "prop": "P108"}]
    captured = {}

    def _fake_verify(name, signal, candidate):
        captured["candidate"] = candidate
        return ("unknown", 0.0)

    monkeypatch.setattr(enrich_mod.llm_classify, "verify_identity", _fake_verify)
    subj = _person(db, "Jane Investor")

    e._identity_confirmed(subj, "Q999", None)

    assert "Different Capital" in captured["candidate"]
    assert "Another Fund" in captured["candidate"]
    assert "American venture capitalist" in captured["candidate"]  # nothing lost


def test_no_org_names_leaves_the_candidate_signal_unchanged(db, monkeypatch):
    """A candidate with no listed affiliations degrades to the old behaviour —
    this is additive information, not a replacement for the description."""
    e = Enricher()
    e._verify_identity = True
    e._background_text = "partner at Acme Capital, a seed-stage fund"
    e._hint = ""
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    e.wikidata.orgs_for_person = lambda qid: []
    captured = {}

    def _fake_verify(name, signal, candidate):
        captured["candidate"] = candidate
        return ("unknown", 0.0)

    monkeypatch.setattr(enrich_mod.llm_classify, "verify_identity", _fake_verify)
    subj = _person(db, "Jane Investor")

    e._identity_confirmed(subj, "Q999", None)

    assert captured["candidate"] == "American venture capitalist investor"


def test_org_names_are_capped_at_five(db, monkeypatch):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "partner at Acme Capital"
    e._hint = ""
    e.wikidata.identity_card = lambda qid: {
        "description": "venture capitalist", "occupations": []}
    e.wikidata.orgs_for_person = lambda qid: [
        {"org_name": f"Fund {i}", "org_qid": f"Q{i}", "prop": "P108"} for i in range(8)]
    captured = {}

    def _fake_verify(name, signal, candidate):
        captured["candidate"] = candidate
        return ("unknown", 0.0)

    monkeypatch.setattr(enrich_mod.llm_classify, "verify_identity", _fake_verify)
    subj = _person(db, "Jane Investor")

    e._identity_confirmed(subj, "Q999", None)

    kept = [f"Fund {i}" for i in range(8) if f"Fund {i}" in captured["candidate"]]
    assert len(kept) == 5
