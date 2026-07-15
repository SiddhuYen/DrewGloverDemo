"""FastAPI app: /connect, /discover, /health, /ui.

Single uvicorn worker + one SQLite file. connect() writes to the graph while it
enriches, so requests are serialized behind a lock — the demo is single-user by
design and this keeps SQLite writers from contending.
"""
from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from . import config
from .db import get_db, init_db
from .edges import taxonomy
from .graph.connect import connect_people, discover
from .graph.tree import build_tree, compare_trees
from .ingest.linkedin_csv import ingest_csv
from .ingest.seed import seed_drew
from .models import Organization, Person, RelationshipEdge, Source
from .paths import resource_path
from .providers.brave import brave_status, set_key as set_brave_key
from .providers.serper import serper_status, set_key as set_serper_key
from .providers.stats import STATS

app = FastAPI(title="VC Warm-Intro Pathfinder", version="1.0")

# Not `Path(__file__).parent`: inside a one-file bundle this module is imported
# from the PYZ archive and `__file__` names a path that does not exist on disk.
_STATIC = resource_path("app", "static")
_write_lock = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    counts = {
        "people": db.scalar(select(func.count()).select_from(Person)),
        "organizations": db.scalar(select(func.count()).select_from(Organization)),
        "edges": db.scalar(select(func.count()).select_from(RelationshipEdge)),
    }
    return {"ok": True, "seed": config.DEMO_SEED_NAME, "graph": counts,
            "serper": serper_status(), "providers": STATS.snapshot()}


@app.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    with _write_lock:
        return seed_drew(db)


@app.get("/search/status")
def search_status() -> dict:
    """Which search engine the next lookup would use, and why."""
    return {"brave": brave_status(), "serper": serper_status(),
            "deep_budget_s": int(config.DEEP_SEARCH_BUDGET_S)}


@app.post("/search/key")
def set_search_key(key: str = Form(default=""),
                   engine: str = Form(default="serper")) -> dict:
    """Install a search key for this run.

    Deliberately not persisted to disk: it is the user's credential, and writing
    it into the app's data dir would outlive the session they typed it in for.
    Put it in a .env beside the .exe to make it permanent.

    Both engines can hold a key at once — they are searched together, not as
    fallbacks, so a second key is more coverage rather than a spare.
    """
    setters = {"serper": set_serper_key, "brave": set_brave_key}
    engine = (engine or "serper").strip().lower()
    if engine not in setters:
        raise HTTPException(status_code=400,
                            detail=f"Unknown engine {engine!r}. Use serper or brave.")
    if not setters[engine](key):
        raise HTTPException(status_code=400,
                            detail=f"That does not look like a {engine} API key.")
    return {"ok": True, "engine": engine,
            "serper": serper_status(), "brave": brave_status()}


@app.get("/connect")
def connect(
    target: str = Query(..., min_length=2, description="who you want to reach"),
    source: str = Query(default="", description="defaults to the demo seed"),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    deep: bool = Query(default=False,
                       description="search the web up to DEEP_SEARCH_BUDGET_S"),
    db: Session = Depends(get_db),
) -> dict:
    source = source or config.DEMO_SEED_NAME
    with _write_lock:
        # A deep search is opt-in per request because it holds the write lock for
        # minutes. The default 40s budget exists to keep the UI responsive; here
        # the user has asked to wait, so raise the ceiling for this call only.
        budget = config.CONNECT_WORK_BUDGET_S
        if deep:
            config.CONNECT_WORK_BUDGET_S = config.DEEP_SEARCH_BUDGET_S
        try:
            return connect_people(db, source, target, depth=depth)
        finally:
            config.CONNECT_WORK_BUDGET_S = budget


@app.get("/discover")
def discover_endpoint(
    person: str = Query(default="", description="defaults to the demo seed"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    person = person or config.DEMO_SEED_NAME
    with _write_lock:
        result = discover(db, person, limit=limit)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@app.get("/tree")
def tree(
    person: str = Query(..., min_length=2),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    max_hops: int = Query(default=3, ge=0, le=8,
                          description="0 = no limit (whole reachable set)"),
    db: Session = Depends(get_db),
) -> dict:
    """The warmest-path network tree rooted at `person`."""
    with _write_lock:
        result = build_tree(db, person, depth=depth, max_hops=max_hops)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@app.get("/compare")
def compare(
    person: str = Query(..., min_length=2, description="whose network to compare"),
    against: str = Query(default="", description="defaults to the demo seed"),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    radius: int = Query(default=config.COMPARE_RADIUS, ge=1, le=4),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Compare `person`'s network against `against` (Drew by default)."""
    against = against or config.DEMO_SEED_NAME
    with _write_lock:
        result = compare_trees(db, against, person, depth=depth, radius=radius,
                               limit=limit)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@app.get("/edges")
def edges(
    q: str = Query(default="", description="filter to connections naming this person"),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    """Every person-person connection currently in the graph, warmest first.

    Read-only, so it takes no write lock and never enriches: this is the graph as
    it stands, not a search. Membership rows (`person_b` is NULL) are excluded —
    they record that someone belongs to an org, which Rule 1 may deliberately
    have refused to turn into contacts, so listing them as connections would
    assert exactly what the graph declined to.
    """
    A, B = aliased(Person), aliased(Person)
    stmt = (
        select(RelationshipEdge, A, B, Source)
        .join(A, RelationshipEdge.person_a_id == A.id)
        .join(B, RelationshipEdge.person_b_id == B.id)
        .outerjoin(Source, RelationshipEdge.source_id == Source.id)
        .where(RelationshipEdge.person_b_id.isnot(None))
        .order_by(RelationshipEdge.warmth_tier, RelationshipEdge.cost)
    )
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(A.canonical_name.ilike(like),
                             B.canonical_name.ilike(like)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.limit(limit)).all()

    return {
        "total": total,
        "shown": len(rows),
        "truncated": total > len(rows),
        "edges": [
            {
                "a": a.canonical_name,
                "b": b.canonical_name,
                "a_warm": bool(a.is_warm),
                "b_warm": bool(b.is_warm),
                "relationship": edge.relationship_type,
                "why": taxonomy.label_for(edge.relationship_type),
                "tier": edge.warmth_tier,
                "evidence": edge.evidence_snippet,
                "url": src.url if src else None,
            }
            for edge, a, b, src in rows
        ],
    }


@app.post("/network/csv")
async def upload_csv(file: UploadFile = File(...),
                     db: Session = Depends(get_db)) -> dict:
    """Optional LinkedIn export -> tier-1 edges from the demo seed."""
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
    with _write_lock:
        return ingest_csv(db, content, owner_name=config.DEMO_SEED_NAME)


@app.get("/ui")
@app.get("/ui/")
def ui() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
