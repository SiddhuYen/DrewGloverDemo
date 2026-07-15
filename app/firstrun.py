"""First-launch setup for the packaged app.

The .exe ships a prebuilt graph so the first query answers instantly instead of
spending minutes crawling. That file lives inside the bundle, which is read-only
and deleted on exit, so it is copied once into the user's data directory and
everything afterwards writes there.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import paths

_BUNDLED_GRAPH = ("seed", "vcwarmintro.db")


def ensure_graph_db() -> Path | None:
    """Install the bundled graph on first launch. Returns the live DB path.

    Never overwrites: after the first run the user's copy is the real one, with
    whatever enrichment their queries have added since. A no-op from source,
    where the developer builds their own graph with `seed`/`precrawl`.
    """
    if not paths.is_frozen():
        return None

    target = paths.user_data_dir() / "vcwarmintro.db"
    if target.exists():
        return target

    src = paths.resource_path(*_BUNDLED_GRAPH)
    if not src.is_file():
        # Shipping without a graph is survivable — the app seeds itself on
        # demand — so this must not be fatal.
        return None
    try:
        # copyfile, not copy2: copy2 carries the source's permission bits across,
        # and a read-only bundled file would land as a read-only database that
        # SQLite cannot write — failing on the user's first query. Clear the flag
        # afterwards too, in case the umask or a copied ACL left it unset.
        shutil.copyfile(src, target)
        target.chmod(target.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        # A partial copy would look like a valid graph; leave nothing behind.
        try:
            os.unlink(target)
        except OSError:
            pass
        return None
    return target
