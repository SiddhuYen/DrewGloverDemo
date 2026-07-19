"""POST /confirm-contact — the correction path for a fame_penalty false
positive: a real contact the tool doesn't yet know is is_warm (see
connect._suggest_known_contact, surfaced on /connect as "warmer_if_known").

Uses its own StaticPool in-memory engine rather than the shared `db` fixture:
a sync FastAPI route runs in a worker thread, and a bare `sqlite://` engine
hands each thread a DIFFERENT in-memory database unless every checkout is
pinned to one physical connection.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.graph import builder
from app.main import app


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


def test_confirm_contact_sets_is_warm():
    db = _threadsafe_db()
    person = builder.get_or_create_person(db, "Some Founder")
    db.commit()
    assert person.is_warm is False

    try:
        resp = _client_for(db).post("/confirm-contact", json={"name": "Some Founder"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "name": "Some Founder", "is_warm": True}
        db.refresh(person)
        assert person.is_warm is True
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_confirm_contact_is_idempotent():
    db = _threadsafe_db()
    person = builder.get_or_create_person(db, "Already Warm", is_warm=True)
    db.commit()
    try:
        resp = _client_for(db).post("/confirm-contact", json={"name": "Already Warm"})
        assert resp.status_code == 200
        assert resp.json()["is_warm"] is True
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_confirm_contact_404s_on_an_unknown_name():
    db = _threadsafe_db()
    try:
        resp = _client_for(db).post("/confirm-contact", json={"name": "Nobody At All"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()
