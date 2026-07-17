"""LLM-triggered structural verification: the gate (_should_verify) and the
targeted roster lookup (_verify_and_promote), plus their wiring into
_from_comention. No network — firms.roster_for_firm and extract.org_names are
monkeypatched throughout."""
from app import config
import app.graph.enrich as enrich_mod
from app.graph import builder
from app.graph.enrich import Enricher


def _person(db, name):
    return builder.get_or_create_person(db, name)


# --- _should_verify: the tier/confidence/label gate -------------------------
def test_should_verify_rejects_unknown_label():
    e = Enricher()
    assert e._should_verify({"label": "unknown", "confidence": 0.99}) is False


def test_should_verify_rejects_a_non_groundable_label():
    """family_member/bandmate/teammate/coauthor already have their own
    dedicated providers earlier in the same enrichment pass — a miss there is
    a real miss, not something this org-roster check can verify."""
    e = Enricher()
    assert e._should_verify({"label": "family_member", "confidence": 0.95}) is False


def test_should_verify_rejects_low_confidence():
    e = Enricher()
    assert e._should_verify({"label": "cofounder", "confidence": 0.5}) is False


def test_should_verify_rejects_a_tier_below_the_threshold(monkeypatch):
    monkeypatch.setattr(config, "LLM_VERIFY_MIN_TIER", 1)
    e = Enricher()
    # "colleague" is tier 3, above a tier-1-only gate
    assert e._should_verify({"label": "colleague", "confidence": 0.9}) is False


def test_should_verify_passes_for_a_confident_groundable_label():
    e = Enricher()
    assert e._should_verify({"label": "cofounder", "confidence": 0.9}) is True
    assert e._should_verify({"label": "board_member", "confidence": 0.8}) is True


# --- _verify_and_promote: the targeted roster lookup ------------------------
def test_verify_and_promote_is_a_noop_with_no_evidence(db):
    e = Enricher()
    a, b = _person(db, "Alice Example"), _person(db, "Bob Example")
    assert e._verify_and_promote(db, a, b, "", None) is False


def test_verify_and_promote_creates_a_real_edge_when_the_roster_confirms(db, monkeypatch):
    e = Enricher()
    a, b = _person(db, "Alice Example"), _person(db, "Bob Example")
    monkeypatch.setattr(enrich_mod.extract, "org_names", lambda text: ["Acme Ventures"])
    e.firms.roster_for_firm = lambda name: {
        "firm": "Acme Ventures", "url": "https://acme.vc/team",
        "members": ["Alice Example", "Bob Example"], "overflow": False,
    }

    ok = e._verify_and_promote(
        db, a, b, "Alice and Bob co-founded Acme Ventures together.", None)

    assert ok is True
    assert builder.has_structural_edge(db, a.id, b.id) is True
    edge = (db.query(builder.RelationshipEdge)
            .filter_by(relationship_type="same_firm_partner").one())
    assert edge.warmth_tier == 1
    assert edge.cost == config.WARMTH_TIER_COST[1]


def test_verify_and_promote_types_the_edge_from_the_roster_not_the_llm_guess(db, monkeypatch):
    """Rule 0: the promoted edge's type comes from what the structural source
    (a team roster) actually supports, never from the LLM's guessed label —
    even though the evidence text used the word "cofounder"."""
    e = Enricher()
    a, b = _person(db, "Alice Example"), _person(db, "Bob Example")
    monkeypatch.setattr(enrich_mod.extract, "org_names", lambda text: ["Acme Ventures"])
    e.firms.roster_for_firm = lambda name: {
        "firm": "Acme Ventures", "url": "https://acme.vc/team",
        "members": ["Alice Example", "Bob Example"], "overflow": False,
    }

    e._verify_and_promote(db, a, b, "Alice and Bob are cofounders of Acme Ventures.", None)

    types = {e.relationship_type for e in db.query(builder.RelationshipEdge).all()
             if e.person_b_id is not None}
    assert types == {"same_firm_partner"}
    assert "cofounder" not in types


def test_verify_and_promote_returns_false_when_roster_lacks_the_other_person(db, monkeypatch):
    e = Enricher()
    a, b = _person(db, "Alice Example"), _person(db, "Bob Example")
    monkeypatch.setattr(enrich_mod.extract, "org_names", lambda text: ["Acme Ventures"])
    e.firms.roster_for_firm = lambda name: {
        "firm": "Acme Ventures", "url": "https://acme.vc/team",
        "members": ["Alice Example", "Someone Else"], "overflow": False,
    }

    ok = e._verify_and_promote(
        db, a, b, "Alice and Bob co-founded Acme Ventures together.", None)

    assert ok is False
    assert builder.has_structural_edge(db, a.id, b.id) is False


def test_verify_and_promote_returns_false_when_no_org_is_extracted(db, monkeypatch):
    e = Enricher()
    a, b = _person(db, "Alice Example"), _person(db, "Bob Example")
    monkeypatch.setattr(enrich_mod.extract, "org_names", lambda text: [])
    ok = e._verify_and_promote(db, a, b, "Alice and Bob used to be close.", None)
    assert ok is False


# --- _from_comention wiring: verify-then-fallback ---------------------------
def test_from_comention_promotes_confident_hits_and_falls_back_for_the_rest(db, monkeypatch):
    e = Enricher()
    subject = _person(db, "Alice Example")
    monkeypatch.setattr(config, "CO_MENTION_ENABLED", True)
    monkeypatch.setattr(e.comention, "co_mentions", lambda name, hint="": [
        {"name": "Bob Example", "source_url": "https://news.example/a",
         "evidence": "Alice and Bob co-founded Acme Ventures together."},
        {"name": "Carol Example", "source_url": "https://news.example/b",
         "evidence": "Alice and Carol posed for a photo at a gala."},
    ])
    monkeypatch.setattr(enrich_mod.llm_classify, "classify", lambda items: [
        {"label": "cofounder", "confidence": 0.9},
        {"label": "unknown", "confidence": 0.0},
    ])
    monkeypatch.setattr(enrich_mod.extract, "org_names",
                        lambda text: ["Acme Ventures"] if "Acme" in text else [])
    e.firms.roster_for_firm = lambda name: {
        "firm": "Acme Ventures", "url": "https://acme.vc/team",
        "members": ["Alice Example", "Bob Example"], "overflow": False,
    }

    created = e._from_comention(db, subject, None)
    assert created == 2  # 1 promoted structural edge + 1 fallback co_mention edge

    bob = builder.get_or_create_person(db, "Bob Example")
    carol = builder.get_or_create_person(db, "Carol Example")
    assert builder.has_structural_edge(db, subject.id, bob.id) is True
    assert builder.has_structural_edge(db, subject.id, carol.id) is False
    carol_edge = (db.query(builder.RelationshipEdge)
                  .filter_by(relationship_type="co_mention").one())
    assert carol_edge.person_b_id in (subject.id, carol.id)


def test_from_comention_skips_verification_when_a_structural_edge_already_exists(
        db, monkeypatch):
    """The free pre-check: Wikidata/EDGAR/etc. already ran earlier in the same
    enrich_person pass, so if they already settled this pair, don't spend the
    extra roster lookup again."""
    e = Enricher()
    subject = _person(db, "Alice Example")
    bob = _person(db, "Bob Example")
    builder.add_edge(db, subject, bob, "board_member")  # already structural

    monkeypatch.setattr(config, "CO_MENTION_ENABLED", True)
    monkeypatch.setattr(e.comention, "co_mentions", lambda name, hint="": [
        {"name": "Bob Example", "source_url": "https://news.example/a",
         "evidence": "Alice and Bob co-founded Acme Ventures together."},
    ])
    monkeypatch.setattr(enrich_mod.llm_classify, "classify", lambda items: [
        {"label": "cofounder", "confidence": 0.9},
    ])

    calls = {"n": 0}

    def fake_roster(name):
        calls["n"] += 1
        return {"firm": name, "url": "", "members": [], "overflow": False}
    e.firms.roster_for_firm = fake_roster
    monkeypatch.setattr(enrich_mod.extract, "org_names", lambda text: ["Acme Ventures"])

    e._from_comention(db, subject, None)
    assert calls["n"] == 0  # verification never attempted
