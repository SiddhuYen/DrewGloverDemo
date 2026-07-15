"""Where files live, running from source vs. frozen into a one-file .exe.

PyInstaller's one-file build unpacks the bundle into a fresh temp directory on
every launch and deletes it on exit. Two kinds of path must therefore never be
confused:

  * `resource_path()` — read-only things shipped INSIDE the bundle: the UI, the
    spaCy model, the prebuilt graph. Lives under `sys._MEIPASS` when frozen.
  * `user_data_dir()` — everything we WRITE: the graph, the cache. Must sit
    outside the bundle or it is destroyed on exit, and must not sit next to the
    .exe either — an app in `Program Files` has no write permission there, so
    the old `./vcwarmintro.db` default would fail on first query.

Running from source both resolve to the repo root and the CWD respectively, so
the developer workflow and every path in the README are unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "VCWarmIntro"


def is_frozen() -> bool:
    """True only inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(*parts: str) -> Path:
    """A read-only file shipped with the app.

    `__file__` is unreliable for this: for a module bundled into the PYZ it is a
    virtual path that has never existed on disk. `sys._MEIPASS` is the only
    documented root.
    """
    if is_frozen():
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def exe_dir() -> Path:
    """The directory the user actually launched, for a `.env` dropped beside it.

    `sys.executable` inside a one-file bundle is the .exe itself, not the temp
    unpack dir — which is exactly what we want here.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Writable per-user directory; created on demand.

    `VCWI_DATA_DIR` overrides it, which is what the build script uses to point a
    throwaway run at a temp graph.
    """
    override = os.environ.get("VCWI_DATA_DIR", "").strip()
    if override:
        base = Path(override)
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = Path(root) / APP_NAME
    else:
        root = (os.environ.get("XDG_DATA_HOME")
                or os.path.join(os.path.expanduser("~"), ".local", "share"))
        base = Path(root) / "vcwarmintro"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        # A read-only or missing LOCALAPPDATA is not worth crashing over at
        # import time; the caller's first write raises with a better message.
        pass
    return base
