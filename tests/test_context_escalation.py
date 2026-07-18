"""Context escalation: when a normal connect finds no structural path but the
user supplied a context and a web-search key is configured, the target is
web-searched for co-mentions (steered by the context) and those weak links
become traversable. No network — the co-mention provider and the LLM classifier
are monkeypatched.
"""
from app import config
import app.graph.enrich as enrich_mod
from app.graph import builder
from app.graph.enrich import Enricher


def _p(db, name):
    return builder.get_or_create_person(db, name)


def _wire_comention(e, hits, monkeypatch):
    monkeypatch.setattr(e.comention, "_available", lambda: True)
    monkeypatch.setattr(e.comention, "co_mentions", lambda name, hint="": hits)
    # the LLM classifier is a no-op here (no key) -> everything "unknown"
    monkeypatch.setattr(enrich_mod.llm_classify, "classify",
                        lambda items: [{"label": "unknown", "confidence": 0.0} for _ in items])


def test_from_comention_stays_off_without_deep_or_force(db, monkeypatch):
    monkeypatch.setattr(config, "DEEP_SEARCH", False)
    monkeypatch.setattr(config, "CO_MENTION_ENABLED", False)
    e = Enricher()
    _wire_comention(e, [{"name": "Web Person", "source_url": "https://x.com/a",
                         "evidence": ""}], monkeypatch)
    subj = _p(db, "Target Person")
    assert e._from_comention(db, subj, None) == 0          # gated off


def test_force_creates_comention_edges_without_deep(db, monkeypatch):
    monkeypatch.setattr(config, "DEEP_SEARCH", False)
    monkeypatch.setattr(config, "CO_MENTION_ENABLED", False)
    e = Enricher()
    _wire_comention(e, [{"name": "Web Person", "source_url": "https://x.com/a",
                         "evidence": "named alongside on a page"}], monkeypatch)
    subj = _p(db, "Target Person")

    created = e._from_comention(db, subj, None, force=True)

    assert created == 1
    other = _p(db, "Web Person")
    from app.models import RelationshipEdge
    edge = db.query(RelationshipEdge).filter_by(person_a_id=min(subj.id, other.id)).first() \
        or db.query(RelationshipEdge).first()
    assert edge is not None
    assert edge.relationship_type == "co_mention"
    assert edge.warmth_tier >= 6           # the weakest, clearly-labelled tier


def test_enrich_target_comention_is_a_noop_without_web_search(db, monkeypatch):
    """No web-search provider at all (neither Serper nor the DuckDuckGo
    fallback) -> the escalation does nothing, and never touches the network."""
    e = Enricher()
    monkeypatch.setattr(e.comention, "_available", lambda: False)
    assert e.enrich_target_comention(db, "Target Person", hint="vc") == 0


def test_enrich_target_comention_runs_the_forced_pass(db, monkeypatch):
    monkeypatch.setattr(config, "DEEP_SEARCH", False)
    e = Enricher()
    _wire_comention(e, [{"name": "Bridge X", "source_url": "https://x.com/b",
                         "evidence": "co-mentioned"}], monkeypatch)
    created = e.enrich_target_comention(db, "Target Person", hint="fintech founder")
    assert created == 1
    # the context is what the search was steered by
    assert e._hint == "fintech founder"


def test_escalation_makes_a_weak_path_traversable(db, monkeypatch):
    """End-to-end shape: with a context + a (mocked) web-search key, a connect
    that has no structural path picks up a co-mention bridge and returns it."""
    from app.graph import connect as connect_mod

    monkeypatch.setattr(config, "DEEP_SEARCH", False)
    monkeypatch.setattr(config, "CO_MENTION_ENABLED", False)

    drew = _p(db, "Drew Glover")
    bridge = _p(db, "Mutual Bridge")
    # Drew already structurally knows the bridge; the target does not (yet).
    builder.add_edge(db, drew, bridge, "podcast_guest")
    db.commit()

    e = connect_mod.get_enricher()
    # target's web search surfaces the same bridge as a co-mention
    _wire_comention(e, [{"name": "Mutual Bridge", "source_url": "https://x.com/t",
                         "evidence": "named together"}], monkeypatch)
    # keep enrichment from doing anything else / hitting the network (monkeypatch
    # so the shared singleton enricher is restored after the test)
    monkeypatch.setattr(e, "enrich_neighborhood", lambda *a, **k: None)
    monkeypatch.setattr(e, "enrich_person",
                        lambda *a, **k: _p(db, a[1] if len(a) > 1 else k.get("name")))

    out = connect_mod.connect_people(db, "Drew Glover", "Cold Target",
                                     hint="fintech founder")

    assert out["connected"] is True
    labels = [n["label"] for n in out["paths"][0]["path"]]
    assert labels[0] == "Drew Glover" and labels[-1] == "Cold Target"
    assert "Mutual Bridge" in labels
