"""FastAPI app: /connect, /discover, /health, /ui.

Single uvicorn worker + one SQLite file. connect() writes to the graph while it
enriches, so requests are serialized behind a lock — the demo is single-user by
design and this keeps SQLite writers from contending.
"""
from __future__ import annotations

import json
import os
import threading

from fastapi import Body, Depends, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .db import get_db, init_db
from .graph.connect import connect_people, discover
from .graph.tree import build_tree, compare_trees
from .ingest.linkedin_csv import ingest_csv
from .ingest.seed import seed_drew
from .models import Organization, Person, RelationshipEdge
from .providers.serper import serper_status
from .providers.stats import STATS

app = FastAPI(title="VC Warm-Intro Pathfinder", version="1.0")

_STATIC = Path(__file__).parent / "static"
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


@app.get("/settings")
def get_settings() -> dict:
    """Whether live search is configured. Never returns the key itself."""
    return {"serper_configured": bool(config.SERPER_API_KEY),
            "serper": serper_status()}


@app.post("/settings")
def set_settings(serper_key: str = Body(..., embed=True)) -> dict:
    """Store the user's own Serper API key (desktop app) and apply it live, so
    a new target can be searched without shipping anyone's key."""
    key = (serper_key or "").strip()
    config.SERPER_API_KEY = key                 # SerperProvider reads this live
    os.environ["SERPER_API_KEY"] = key
    path = os.environ.get("VCWI_SETTINGS_FILE")
    if path:
        try:
            json.dump({"serper_key": key}, open(path, "w"))
        except Exception as exc:
            raise HTTPException(500, f"could not persist settings: {exc}")
    return {"ok": True, "serper_configured": bool(key)}


@app.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    with _write_lock:
        return seed_drew(db)


@app.get("/connect")
def connect(
    target: str = Query(..., min_length=2, description="who you want to reach"),
    source: str = Query(default="", description="defaults to the demo seed"),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    db: Session = Depends(get_db),
) -> dict:
    source = source or config.DEMO_SEED_NAME
    with _write_lock:
        return connect_people(db, source, target, depth=depth)


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


@app.get("/download")
@app.get("/download/")
def download() -> FileResponse:
    return FileResponse(_STATIC / "download.html")


if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
