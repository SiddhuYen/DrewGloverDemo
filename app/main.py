"""FastAPI app: /connect, /discover, /health, /ui.

Single uvicorn worker + one SQLite file. connect() writes to the graph while it
enriches, so requests are serialized behind a lock — the demo is single-user by
design and this keeps SQLite writers from contending.
"""
from __future__ import annotations

import json
import os
import queue
import threading

from fastapi import Body, Depends, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal, get_db, init_db
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
    """Live-search config. Never returns the key itself."""
    return {"serper_configured": bool(config.SERPER_API_KEY),
            "deep_search": config.DEEP_SEARCH, "serper": serper_status()}


@app.post("/settings")
def set_settings(serper_key: str = Body(None, embed=True),
                 deep_search: bool = Body(None, embed=True)) -> dict:
    """Store the user's own Serper key and/or the deep-search toggle, apply live,
    and persist. `deep_search` = the ArtemisV2-style 2-hop web expansion."""
    path = os.environ.get("VCWI_SETTINGS_FILE")
    cur = {}
    if path and os.path.exists(path):
        try:
            cur = json.load(open(path))
        except Exception:
            cur = {}
    if serper_key is not None:
        key = serper_key.strip()
        config.SERPER_API_KEY = key             # SerperProvider reads this live
        os.environ["SERPER_API_KEY"] = key
        cur["serper_key"] = key
    if deep_search is not None:
        config.DEEP_SEARCH = bool(deep_search)  # _adjacency / enrich read this live
        cur["deep_search"] = bool(deep_search)
    if path:
        try:
            json.dump(cur, open(path, "w"))
        except Exception as exc:
            raise HTTPException(500, f"could not persist settings: {exc}")
    return {"ok": True, "serper_configured": bool(config.SERPER_API_KEY),
            "deep_search": config.DEEP_SEARCH}


@app.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    with _write_lock:
        return seed_drew(db)


@app.get("/connect")
def connect(
    target: str = Query(..., min_length=2, description="who you want to reach"),
    source: str = Query(default="", description="defaults to the demo seed"),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    context: str = Query(default="", description="disambiguating hint, e.g. 'biotech founder'"),
    db: Session = Depends(get_db),
) -> dict:
    source = source or config.DEMO_SEED_NAME
    with _write_lock:
        return connect_people(db, source, target, depth=depth, hint=context)


@app.get("/discover")
def discover_endpoint(
    person: str = Query(default="", description="defaults to the demo seed"),
    limit: int = Query(default=20, ge=1, le=100),
    context: str = Query(default=""),
    db: Session = Depends(get_db),
) -> dict:
    person = person or config.DEMO_SEED_NAME
    with _write_lock:
        result = discover(db, person, limit=limit, hint=context)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("reason"))
    return result


@app.get("/tree")
def tree(
    person: str = Query(..., min_length=2),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    max_hops: int = Query(default=3, ge=0, le=8,
                          description="0 = no limit (whole reachable set)"),
    context: str = Query(default=""),
    db: Session = Depends(get_db),
) -> dict:
    """The warmest-path network tree rooted at `person`."""
    with _write_lock:
        result = build_tree(db, person, depth=depth, max_hops=max_hops, hint=context)
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


def _sse(work):
    """Run `work(db, progress)` on a worker thread and stream its progress lines
    as Server-Sent Events, then a final `result` event with the returned dict.

    Long cold queries (deep search can take ~150s) otherwise look frozen — this
    lets the UI show a live progress bar of what each enrichment step is doing.
    """
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def run() -> None:
        db = SessionLocal()
        try:
            with _write_lock:
                box["result"] = work(db, lambda m: q.put(("progress", str(m))))
        except Exception as exc:  # surface as an honest failure, don't hang the stream
            box["result"] = {"found": False, "connected": False,
                             "reason": f"error: {exc}"}
        finally:
            db.close()
            q.put(("done", None))

    threading.Thread(target=run, daemon=True).start()

    def gen():
        while True:
            kind, msg = q.get()
            if kind == "done":
                yield f"event: result\ndata: {json.dumps(box.get('result', {}))}\n\n"
                return
            yield f"event: progress\ndata: {json.dumps(msg)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/connect/stream")
def connect_stream(
    target: str = Query(..., min_length=2),
    source: str = Query(default=""),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    context: str = Query(default=""),
):
    src = source or config.DEMO_SEED_NAME
    return _sse(lambda db, p: connect_people(db, src, target, depth=depth,
                                             hint=context, progress=p))


@app.get("/discover/stream")
def discover_stream(
    person: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    context: str = Query(default=""),
):
    who = person or config.DEMO_SEED_NAME
    return _sse(lambda db, p: discover(db, who, limit=limit, hint=context, progress=p))


@app.get("/tree/stream")
def tree_stream(
    person: str = Query(..., min_length=2),
    depth: int = Query(default=config.CONNECT_DEPTH, ge=1, le=3),
    max_hops: int = Query(default=3, ge=0, le=8),
    context: str = Query(default=""),
):
    return _sse(lambda db, p: build_tree(db, person, depth=depth,
                                         max_hops=max_hops, hint=context, progress=p))


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
