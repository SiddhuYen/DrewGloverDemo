"""Map Drew's FAMOUS network — a one-time "push up the fame gradient" build.

The per-query target expansion walks DOWN the fame gradient: from a cold famous
person it prefers the least-notable neighbours, heading toward Drew's ordinary
reachable world (see graph/bridge.py). This script does the opposite, once, from
Drew: it prefers the MOST notable, highest-degree neighbours, walking UP into the
celebrity / out-of-fintech network he can reach.

Why: a famous person Drew reaches (a VC who's been on Rogan, say) is a hub into a
huge network beyond fintech. Enriching those hubs proactively builds a standing
"famous backbone", so later queries to out-of-domain targets are short and
instant instead of rediscovered per query. The graph is persistent (SQLite), so
this accumulates and every run only pays for newly-reached people.

    ./.venv/bin/python scripts/build_famous_backbone.py --depth 4 --fanout 8

Bounded by a wall-clock budget; it fails loudly, reporting what it skipped.
Serper/podcast/browser costs apply per newly-enriched person — run it when you
can spare a few minutes, and re-run later to extend it.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter, deque              # noqa: E402

from sqlalchemy import select                       # noqa: E402

from app import config                              # noqa: E402
from app.db import SessionLocal, init_db            # noqa: E402
from app.edges.names import person_norm_key         # noqa: E402
from app.graph.bridge import is_notable             # noqa: E402
from app.graph.connect import _adjacency            # noqa: E402
from app.graph.enrich import get_enricher           # noqa: E402
from app.models import Person                        # noqa: E402


def _reach(db, root_id):
    adj, _by, _s, _p = _adjacency(db)
    seen = {root_id}
    q = deque([root_id])
    while q:
        n = q.popleft()
        for nb, _e in adj.get(n, []):
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return seen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="build Drew's famous network by pushing up the fame gradient")
    parser.add_argument("--seed", default=config.DEMO_SEED_NAME)
    parser.add_argument("--depth", type=int, default=4,
                        help="how many hops to walk outward")
    parser.add_argument("--fanout", type=int, default=8,
                        help="famous people enriched per hop")
    parser.add_argument("--budget", type=float, default=600.0,
                        help="wall-clock seconds")
    args = parser.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        root = db.execute(select(Person).where(
            Person.norm_name == person_norm_key(args.seed))).scalar_one_or_none()
        before = len(_reach(db, root.id)) if root else 0
        enriched_before = db.query(Person).filter(Person.enriched == 1).count()

        print(f"pushing UP the fame gradient from {args.seed} "
              f"(depth {args.depth}, fanout {args.fanout}, budget {args.budget:.0f}s)\n")
        get_enricher().enrich_neighborhood(
            db, args.seed, depth=args.depth, prefer_notable=True,
            fanout=args.fanout, deadline=time.monotonic() + args.budget,
            progress=lambda m: print(m, file=sys.stderr, flush=True))

        root = db.execute(select(Person).where(
            Person.norm_name == person_norm_key(args.seed))).scalar_one()
        after = len(_reach(db, root.id))
        enriched_after = db.query(Person).filter(Person.enriched == 1).count()

        # Who are the notable hubs now in Drew's reach?
        adj, by_id, _s, _p = _adjacency(db)
        reach = _reach(db, root.id)
        famous = [(len(adj.get(pid, [])), by_id[pid].canonical_name)
                  for pid in reach
                  if pid != root.id and by_id[pid].wikidata_qid]
        famous.sort(reverse=True)

        print(f"\nreach: {before} -> {after}   "
              f"enriched: {enriched_before} -> {enriched_after}")
        print(f"notable people now reachable: {len(famous)}")
        for deg, name in famous[:20]:
            print(f"   deg {deg:4}  {name}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
