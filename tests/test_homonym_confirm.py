"""Fix: a homonym-guard rejection used to be unrecoverable.

enrich._identity_confirmed can reject a name-matched Wikidata candidate for the
explicit search target. Before this fix, that rejection was a log line only:
enrich_person unconditionally bumps subject.enriched at the end of its pass, so
its idempotency check (`if subject.enriched >= target_enrichment_level()`)
skips this person on every later call — the rejection, right or wrong, was
permanent, with no way to revisit it short of a direct DB edit.

These test that (1) a rejection is persisted on the person rather than just
logged, (2) it is surfaced through connect()/discover() as `identity_uncertain`
so a caller can see it, (3) POST /confirm-identity lets a human override it,
and (4) a later CONFIRMED identity clears a stale rejection note.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.graph import builder
from app.graph import connect as connect_mod
from app.graph.connect import _homonym_notice
from app.graph.enrich import Enricher
from app.main import app


def _person(db, name):
    return builder.get_or_create_person(db, name)


# --- the rejection is persisted, not just logged ----------------------------
def test_rejected_identity_is_persisted_on_the_person(db, monkeypatch):
    """The persistence happens at the end of enrich_person's full pass, not
    inside _from_wikidata itself, so this drives the real entry point rather
    than the inner method — with every other provider (and the web-background
    search) stubbed out to keep the test off the network."""
    e = Enricher()
    monkeypatch.setattr(
        e, "_web_background",
        lambda name, hint: "founder of a test-prep tutoring and admissions company")
    e.wikipedia.qid_for_name = lambda name, hint="": "Q999"
    e.wikidata.is_human = lambda qid: True
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": ["investor"]}
    for step in ("_from_edgar", "_from_opencorporates", "_from_openalex",
                "_from_propublica", "_from_person_firms", "_from_firm_rosters",
                "_from_podcasts", "_from_comention"):
        monkeypatch.setattr(e, step, lambda db, subject, progress: 0)

    subj = e.enrich_person(db, "Abhimanyu Sharma", is_target=True)

    assert subj.wikidata_qid is None      # the stranger's identity still NOT adopted
    notice = _homonym_notice(subj)
    assert notice == {"name": "Abhimanyu Sharma", "qid": "Q999",
                      "description": "American venture capitalist"}


def test_homonym_notice_is_none_without_a_rejection(db):
    subj = _person(db, "Ordinary Person")
    assert _homonym_notice(subj) is None


def test_confirmed_identity_clears_a_stale_rejection(db):
    """A pass that DOES confirm an identity must clear any earlier rejection
    note recorded for this name — it no longer describes the current state."""
    e = Enricher()
    subj = _person(db, "Someone")
    subj.meta = {"homonym_rejected": {"qid": "Q1", "description": "stale note"}}
    e.wikidata.identity_card = lambda qid: {
        "description": "American venture capitalist", "occupations": []}

    e._store_wikidata_identity(subj, "Q42")

    assert "homonym_rejected" not in (subj.meta or {})
    assert subj.meta["wikidata_desc"] == "American venture capitalist"


# --- surfaced through connect() / discover() --------------------------------
def test_connect_surfaces_the_targets_rejected_identity(db):
    drew = _person(db, "Drew Glover")
    target = _person(db, "Abhimanyu Sharma")
    bridge = _person(db, "Some Bridge")
    builder.add_edge(db, drew, bridge, "cofounder")
    builder.add_edge(db, bridge, target, "cofounder")
    target.meta = {"homonym_rejected": {"qid": "Q999",
                                        "description": "American venture capitalist"}}
    db.commit()

    # Both endpoints already resolve to a route in the existing graph (stage 0),
    # so connect_people never reaches the network-touching enrichment stages.
    out = connect_mod.connect_people(db, "Drew Glover", "Abhimanyu Sharma")

    assert out["connected"] is True
    assert out["identity_uncertain"] == {
        "name": "Abhimanyu Sharma", "qid": "Q999",
        "description": "American venture capitalist"}


def test_connect_has_no_notice_for_an_ordinary_target(db):
    drew = _person(db, "Drew Glover")
    target = _person(db, "Charles Hudson")
    builder.add_edge(db, drew, target, "cofounder")
    db.commit()

    out = connect_mod.connect_people(db, "Drew Glover", "Charles Hudson")

    assert out["identity_uncertain"] is None


def test_discover_surfaces_the_roots_rejected_identity(db):
    root = _person(db, "Abhimanyu Sharma")
    root.enriched = 99          # already enriched: discover() must not re-enrich
    root.meta = {"homonym_rejected": {"qid": "Q999",
                                      "description": "American venture capitalist"}}
    other = _person(db, "Some Contact")
    builder.add_edge(db, root, other, "cofounder")
    db.commit()

    out = connect_mod.discover(db, "Abhimanyu Sharma")

    assert out["found"] is True
    assert out["identity_uncertain"] == {
        "name": "Abhimanyu Sharma", "qid": "Q999",
        "description": "American venture capitalist"}


# --- POST /confirm-identity --------------------------------------------------
# Uses its own StaticPool in-memory engine rather than the shared `db` fixture:
# a sync FastAPI route runs in a worker thread, and a bare `sqlite://` engine
# hands each thread a DIFFERENT in-memory database unless every checkout is
# pinned to one physical connection.
def _client_for(db) -> TestClient:
    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _threadsafe_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    import app.models  # noqa: F401  (register mappers)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)()


def test_confirm_identity_adopts_the_qid_and_clears_the_rejection(monkeypatch):
    db = _threadsafe_db()
    person = builder.get_or_create_person(db, "Abhimanyu Sharma")
    person.meta = {"homonym_rejected": {"qid": "Q999", "description": "..."}}
    db.commit()

    # A forced re-enrich would otherwise hit the network via every provider;
    # this endpoint's job is to adopt the identity and retry, not to prove
    # every provider works, so the retry itself is stubbed out.
    import app.graph.enrich as enrich_mod
    monkeypatch.setattr(enrich_mod.Enricher, "enrich_person",
                        lambda self, db, name, **k: builder.get_or_create_person(db, name))

    try:
        resp = _client_for(db).post("/confirm-identity",
                                    json={"name": "Abhimanyu Sharma", "qid": "Q999"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "name": "Abhimanyu Sharma",
                               "wikidata_qid": "Q999"}
        db.refresh(person)
        assert person.wikidata_qid == "Q999"
        assert "homonym_rejected" not in (person.meta or {})
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_confirm_identity_404s_on_an_unknown_name():
    db = _threadsafe_db()
    try:
        resp = _client_for(db).post("/confirm-identity",
                                    json={"name": "Nobody At All", "qid": "Q1"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()
