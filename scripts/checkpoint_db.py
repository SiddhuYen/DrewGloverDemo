"""Fold the WAL back into the .db and compact it, before bundling.

The packaged app ships `vcwarmintro.db` alone, so anything still sitting in the
`-wal` sidecar would be silently missing from the shipped graph. Called by
build_windows.ps1; harmless to run by hand.

    python scripts/checkpoint_db.py build_assets\vcwarmintro.db

A file rather than `python -c`: PowerShell strips the inner double quotes when it
hands an inline script to a native exe, so the SQL arrived unquoted and Python
died on a syntax error.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def checkpoint(path: str) -> None:
    # isolation_level=None keeps the connection in autocommit: sqlite3 otherwise
    # holds a transaction open, and VACUUM cannot run inside one.
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: checkpoint_db.py <path-to-sqlite-db>", file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print(f"no such database: {target}", file=sys.stderr)
        return 1
    checkpoint(str(target))
    print(f"  checkpointed {target.name} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
