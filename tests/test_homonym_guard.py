"""Homonym guard: a searched person whose NAME matches a notable stranger's
Wikidata page must not adopt that stranger's identity (and inherit their
colleagues/family). Covers the deterministic domain check, the _identity_confirmed
gate, and its wiring into _from_wikidata. No network — the providers are
monkeypatched, and no Claude key is configured so verify_identity is a no-op and
the deterministic backstop decides.
"""
from app import config
from app.graph import disambiguate
from app.graph import builder
from app.graph.enrich import Enricher


def _person(db, name):
    return builder.get_or_create_person(db, name)


# --- disambiguate.domain_conflict (pure) -----------------------------------
def test_domain_conflict_vc_vs_education():
    # the reported bug: Pantheon Prep (education) vs. a VC fund of the same name
    assert disambiguate.domain_conflict(
        "co-founder at Pantheon Prep, a test prep and admissions tutoring company",
        "American venture capitalist and investor",
    ) is True


def test_domain_conflict_same_domain_is_silent():
    assert disambiguate.domain_conflict(
        "general partner at a venture capital fund",
        "venture capitalist",
    ) is False


def test_domain_conflict_is_silent_without_signal_on_either_side():
    assert disambiguate.domain_conflict("", "venture capitalist") is False
    assert disambiguate.domain_conflict("some educator tutor", "") is False
    # neither side anchors in a known domain -> cannot claim a conflict
    assert disambiguate.domain_conflict("a nice person from ohio", "someone") is False


# --- _identity_confirmed: the gate -----------------------------------------
def test_identity_confirmed_fails_open_for_non_target(db):
    """Frontier people arrive keyed by QID from claims; the guard is off."""
    e = Enricher()
    e._verify_identity = False
    subj = _person(db, "Abhimanyu Sharma")
    assert e._identity_confirmed(subj, "Q999", None) is True


def test_identity_confirmed_fails_open_when_candidate_has_no_description(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "test prep educator"
    e.wikidata.identity_card = lambda qid: {"description": "", "occupations": []}
    subj = _person(db, "Abhimanyu Sharma")
    assert e._identity_confirmed(subj, "Q999", None) is True


def test_identity_confirmed_fails_open_without_any_context(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = ""      # no web background
    e._hint = ""                 # and no user context
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    subj = _person(db, "Abhimanyu Sharma")
    assert e._identity_confirmed(subj, "Q999", None) is True


def test_identity_confirmed_rejects_cross_domain_homonym(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = ("Abhimanyu Sharma, co-founder of Pantheon Prep, a "
                          "test-prep and college admissions tutoring startup")
    e.wikidata.identity_card = lambda qid: {
        "description": "Indian venture capitalist and fund manager",
        "occupations": ["venture capitalist"]}
    subj = _person(db, "Abhimanyu Sharma")
    assert e._identity_confirmed(subj, "Q999", None) is False


def test_identity_confirmed_accepts_matching_domain(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "partner at a seed-stage venture capital fund"
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    subj = _person(db, "Jane Investor")
    assert e._identity_confirmed(subj, "Q999", None) is True


def test_identity_confirmed_honors_the_user_hint_alone(db):
    """Even with no web background, a user-typed context that conflicts is
    enough to reject the wrong namesake."""
    e = Enricher()
    e._verify_identity = True
    e._background_text = ""
    e._hint = "test prep tutoring founder"
    e.wikidata.identity_card = lambda qid: {
        "description": "venture capital investor", "occupations": ["financier"]}
    subj = _person(db, "Abhimanyu Sharma")
    assert e._identity_confirmed(subj, "Q999", None) is False


# --- _from_wikidata: the guard is actually wired in -------------------------
def test_from_wikidata_skips_a_mismatched_namesake(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "founder of a test-prep tutoring and admissions company"
    e._hint = ""
    e.wikipedia.qid_for_name = lambda name, hint="": "Q999"
    e.wikidata.is_human = lambda qid: True
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    subj = _person(db, "Abhimanyu Sharma")

    created = e._from_wikidata(db, subj, None)

    assert created == 0
    assert subj.wikidata_qid is None      # the stranger's identity was NOT adopted


def test_from_wikidata_adopts_a_matching_namesake(db):
    e = Enricher()
    e._verify_identity = True
    e._background_text = "general partner at a venture capital firm"
    e._hint = ""
    e.wikipedia.qid_for_name = lambda name, hint="": "Q42"
    e.wikidata.is_human = lambda qid: True
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    # no orgs/family/etc, so the pass adopts the QID but finds 0 edges
    e.wikidata.orgs_for_person = lambda qid: []
    e._from_wikidata_family = lambda db, s, qid, p: 0
    e._from_wikidata_cofounders = lambda db, s, qid, p: 0
    e._from_wikidata_entertainment = lambda db, s, qid, p: 0
    subj = _person(db, "Jane Investor")

    e._from_wikidata(db, subj, None)

    assert subj.wikidata_qid == "Q42"     # matching identity IS adopted


def test_from_wikidata_target_flag_off_keeps_old_behavior(db):
    """With is_target False (a frontier person), no verification runs even if a
    background were set — the QID is adopted as before."""
    e = Enricher()
    e._verify_identity = False
    e.wikipedia.qid_for_name = lambda name, hint="": "Q7"
    e.wikidata.is_human = lambda qid: True
    e.wikidata.identity_card = lambda qid: {
        "description": "venture capitalist", "occupations": []}
    e.wikidata.orgs_for_person = lambda qid: []
    e._from_wikidata_family = lambda db, s, qid, p: 0
    e._from_wikidata_cofounders = lambda db, s, qid, p: 0
    e._from_wikidata_entertainment = lambda db, s, qid, p: 0
    subj = _person(db, "Someone Common")

    e._from_wikidata(db, subj, None)

    assert subj.wikidata_qid == "Q7"
