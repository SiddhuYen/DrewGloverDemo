"""Seed Drew's warm layer, then run sample connects for eyeballing.

    ./.venv/bin/python scripts/demo.py

The last target is deliberately unreachable. A demo that only shows successes
hides the property that matters most: when no structurally-asserted path exists,
this system says so instead of inventing one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                    # noqa: E402
from app.cli import _print_paths                          # noqa: E402
from app.db import SessionLocal, init_db                  # noqa: E402
from app.graph.connect import connect_people, discover    # noqa: E402
from app.ingest.seed import seed_drew                     # noqa: E402

TARGETS = [
    ("Marcos Fernandez", "a Fiat co-founder — expect a 1-hop tier-1 path"),
    ("Bree Hanson",      "a podcast host who interviewed him — 1 hop, tier 1"),
    ("Charles Hudson",   "Precursor Ventures — reachable via the DWAVC hosts"),
    ("Sheel Mohnot",     "Better Tomorrow Ventures — same bridge, different guest"),
    ("Tae Hea Nahm",     "Storm Ventures — the most recent DWAVC guest"),
    ("Sam Altman",       "NOT in his network — expect an honest 'no path'"),
]


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        print("=" * 72)
        print(f"SEEDING {config.DEMO_SEED_NAME}'S FIRST DEGREE")
        print("=" * 72)
        print(seed_drew(db, progress=lambda m: print(m)), "\n")

        print("=" * 72)
        print("DISCOVER — warmest people around him")
        print("=" * 72)
        result = discover(db, config.DEMO_SEED_NAME, limit=10)
        for person in result.get("neighborhood", []):
            star = " *" if person["is_warm"] else ""
            print(f"  {person['warmth_score']:>5}  {person['hops']}h  "
                  f"{person['label']}{star}  — {person['via']}")

        for name, note in TARGETS:
            print("\n" + "=" * 72)
            print(f"CONNECT -> {name}")
            print(f"  ({note})")
            print("=" * 72)
            _print_paths(connect_people(db, config.DEMO_SEED_NAME, name))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
