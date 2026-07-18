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
import uuid
from typing import List

from fastapi import Body, Depends, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal, get_db, init_db
from .graph.connect import connect_people, discover
from .graph.enrich import enrich_selected
from .ingest.linkedin_csv import ingest_csv
from .ingest.seed import seed_drew
from . import session
from .models import Organization, Person, RelationshipEdge
from .providers.serper import serper_status
from .providers.stats import STATS

app = FastAPI(title="VC Warm-Intro Pathfinder", version="1.0")

_STATIC = Path(__file__).parent / "static"
_write_lock = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.middleware("http")
async def _bind_session(request, call_next):
    """Resolve this request's session before anything reads a credential.

    Issues an id on first contact so a visitor can POST a key immediately. The
    cookie carries only the opaque id — the key stays in the server-side store
    (session.py). HttpOnly keeps page scripts out of it; SameSite=Lax keeps a
    third-party page from driving this API as the visitor.
    """
    sid = request.cookies.get(session.COOKIE_NAME)
    fresh = not session.touch(sid)
    if fresh:
        sid = session.new_session()
    token = session.bind(sid)
    try:
        response = await call_next(request)
    finally:
        session.reset(token)
    if fresh:
        response.set_cookie(
            session.COOKIE_NAME, sid, httponly=True, samesite="lax",
            max_age=session.SESSION_TTL_S,
            secure=config.cookie_secure_for(request.url.scheme),
        )
    return response


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
    """Live-search config. Never returns either key itself."""
    return {"serper_configured": bool(config.SERPER_API_KEY),
            "claude_configured": session.claude_configured(),
            "claude_model": config.CLAUDE_MODEL,
            "deep_search": config.DEEP_SEARCH, "serper": serper_status()}


@app.post("/claude-key")
def set_claude_key(claude_key: str = Body(..., embed=True)) -> dict:
    """Hold this visitor's Anthropic key for their session only.

    Deliberately not merged into /settings: that endpoint persists to a shared
    file on disk and mutates process-wide config, which is exactly what a
    credential must not do on a multi-visitor deployment. This one writes to
    the per-session store and never echoes the key back.
    """
    if not session.set_claude_key(claude_key):
        raise HTTPException(400, "no active session; enable cookies and retry")
    return {"ok": True, "claude_configured": session.claude_configured()}


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
            try:
                kind, msg = q.get(timeout=config.SSE_HEARTBEAT_S)
            except queue.Empty:
                # A long enrichment step has emitted nothing for a while. Send an
                # SSE comment (ignored by EventSource) so the idle connection is
                # not dropped by a proxy / Codespaces port-forward / the browser
                # mid-deep-search — the "connection lost" bug.
                yield ": keepalive\n\n"
                continue
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


# Enrichment selections are handed off by id rather than passed in the stream
# URL: an EventSource can only GET, and a few hundred picked names would blow
# past the request-line limit.
_enrich_jobs: dict = {}


@app.post("/network/enrich")
def start_enrich(names: List[str] = Body(..., embed=True)) -> dict:
    """Register a chosen set of imported people for enrichment."""
    picked = [n.strip() for n in names if n and n.strip()]
    if not picked:
        raise HTTPException(400, "no people selected")
    # A selection that is registered but never streamed (the user closed the tab)
    # would otherwise be held forever; keep only the most recent handful.
    while len(_enrich_jobs) >= 32:
        _enrich_jobs.pop(next(iter(_enrich_jobs)))
    job_id = uuid.uuid4().hex
    _enrich_jobs[job_id] = picked
    return {"job_id": job_id, "count": len(picked)}


@app.get("/network/enrich/stream")
def enrich_stream(job: str = Query(..., min_length=1)):
    """Enrich a registered selection, streaming per-person progress."""
    names = _enrich_jobs.pop(job, None)   # single-use: a reload can't re-spend quota
    if names is None:
        raise HTTPException(404, "unknown or already-started enrichment job")
    return _sse(lambda db, p: enrich_selected(db, names, progress=p))


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
