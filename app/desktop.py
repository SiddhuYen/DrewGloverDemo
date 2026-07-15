"""Entry point for the packaged Windows app: serve the UI, open a browser.

Double-clicking the .exe lands here. It is deliberately the only piece that
knows about the desktop: `main.py` stays a plain FastAPI app that still runs
under `uvicorn app.main:app` from source.
"""
from __future__ import annotations

import multiprocessing
import socket
import sys
import threading
import time
import webbrowser

PREFERRED_PORT = 8765
BANNER = """
  VC Warm-Intro Pathfinder
  ---------------------------------------------------
  Finds the warmest real introduction path from
  {seed} to anyone in the VC/startup world.
"""


def _free_port(preferred: int = PREFERRED_PORT) -> int:
    """`preferred` if it is free, else whatever the OS hands out.

    Binding a fixed port would fail outright on the second launch, or when
    anything else on the machine already holds 8765.
    """
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    return preferred


def _open_when_ready(url: str, port: int, timeout: float = 30.0) -> None:
    """Open the browser once the server accepts connections.

    A fixed sleep races: on a cold start spaCy and the graph take seconds to
    load, and a browser that arrives first shows a connection error the user
    then has to reload past.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.25)
    webbrowser.open(url)


def main() -> int:
    multiprocessing.freeze_support()  # a frozen child would re-run this module

    from .console import enable_utf8
    enable_utf8()  # provider progress lines carry non-ASCII too

    from . import config
    print(BANNER.format(seed=config.DEMO_SEED_NAME))

    from .firstrun import ensure_graph_db
    installed = ensure_graph_db()
    if installed:
        print(f"  Installed the bundled graph -> {installed}")

    from . import paths
    print(f"  Your data: {paths.user_data_dir()}")

    import uvicorn
    from .main import app

    port = _free_port()
    url = f"http://127.0.0.1:{port}/ui"
    print(f"  Opening {url}")
    print("  Close this window to quit.\n")

    threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
