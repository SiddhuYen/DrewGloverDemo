"""Desktop entry point — wraps the FastAPI app in a native window.

Starts the uvicorn server on a loopback port in a background thread, waits for
it to answer, then opens a pywebview window pointed at /ui. Live enrichment
works: a new target is searched and pulled into the graph on demand.

State lives in a per-user, WRITABLE data directory (the app bundle is read-only):
  - graph.db    the relationship graph. Seeded on first run from the bundled
                snapshot so the app opens warm.
  - cache.db    the provider cache, built up as the app runs.
  - settings.json  the user's own Serper API key (entered in the UI), so live
                search works without shipping anyone's key.

The bundle ALSO ships a read-only resources/claude_key.txt, baked in at CI
build time from a repo secret (see DESKTOP.md) — a real Anthropic key,
spend-capped in the Anthropic Console so a worst-case extraction is bounded
in dollars. Unlike settings.json this one is never written by the app;
it's just read once at startup.

Env is set BEFORE `app` is imported, because app/config.py reads these at import.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "WarmIntroPathfinder"
APP_AUTHOR = "Pantheon"


def _resource_dir() -> Path:
    """Where bundled read-only resources live: the PyInstaller bundle at
    runtime, or the repo root in development."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    from platformdirs import user_data_dir
    d = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _configure_env() -> Path:
    """Point the app at writable paths + the user's key. Returns the data dir."""
    data = _data_dir()
    graph = data / "graph.db"
    if not graph.exists():
        seed = _resource_dir() / "resources" / "graph.db"
        if seed.exists():
            shutil.copy(seed, graph)      # open warm on first run
    os.environ["VCWI_DB_URL"] = f"sqlite:///{graph}"
    os.environ["VCWI_CACHE_DB"] = str(data / "cache.db")

    settings = data / "settings.json"
    if settings.exists():
        try:
            s = json.loads(settings.read_text())
            key = (s.get("serper_key") or "").strip()
            if key:
                os.environ["SERPER_API_KEY"] = key
            if s.get("deep_search"):
                os.environ["VCWI_DEEP_SEARCH"] = "1"
        except Exception:
            pass
    # tell the app where to persist a key the user types into the settings UI
    os.environ["VCWI_SETTINGS_FILE"] = str(settings)

    # baked-in Claude key (see module docstring). A dev env var, if already
    # set, wins — this only fills the gap in a built app.
    baked = _resource_dir() / "resources" / "claude_key.txt"
    if baked.exists() and "CLAUDE_API_KEY" not in os.environ:
        try:
            key = baked.read_text().strip()
            if key:
                os.environ["CLAUDE_API_KEY"] = key
        except Exception:
            pass
    return data


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(port: int) -> None:
    import uvicorn
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code:
                return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    _configure_env()
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_ready(port):
        print("server failed to start", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/ui"

    if "--server-only" in sys.argv:        # headless test / CI smoke
        print(f"READY {url}", flush=True)
        while True:
            time.sleep(3600)

    import webview
    webview.create_window("Warm-Intro Pathfinder", url, width=1180, height=860,
                          min_size=(900, 640))
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
