"""CLI: seed, connect, discover, stats.

    python -m app.cli seed --discover
    python -m app.cli connect  "Drew Glover" "Charles Hudson"
    python -m app.cli discover "Drew Glover"
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .db import SessionLocal, init_db
from .graph.connect import connect_people, discover
from .ingest.seed import seed_drew


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _print_paths(result: dict) -> None:
    if not result.get("connected"):
        print(f"\n  NO PATH: {result.get('reason', 'unknown')}\n")
        return

    print(f"\n{result['person_a']}  ->  {result['person_b']}")
    print(f"{len(result['paths'])} distinct route(s), warmest first\n")
    for i, path in enumerate(result["paths"], 1):
        print(f"  ── Route {i}: {path['hops']} hop(s), "
              f"warmth {path['warmth_score']} (cost {path['total_cost']})")
        for node in path["path"]:
            rel = node.get("relationship_from_prev")
            if not rel:
                print(f"       {node['label']}")
                continue
            warm = " ★" if node.get("is_warm") else ""
            print(f"         │  {node['why']}  [tier {node['warmth_tier']} · {rel}]")
            if node.get("evidence_snippet"):
                print(f"         │  \"{node['evidence_snippet']}\"")
            if node.get("source_url"):
                print(f"         │  {node['source_url']}")
            print(f"       {node['label']}{warm}")
        print()
    for warning in result.get("warnings", []):
        print(f"  ! {warning}")
    print()


_TIER_MARK = {1: "●", 2: "◐", 3: "◑", 4: "◔", 5: "○"}


def _ellipsis(text: str, width: int) -> str:
    """Clip prose, and SAY that it was clipped."""
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"



def _print_chain(root: str, chain: list, indent: str, show_links: bool) -> None:
    """One introduction chain, hop by hop, with the source that asserts it."""
    print(f"{indent}{root}")
    for hop in chain:
        mark = _TIER_MARK.get(hop["warmth_tier"], "○")
        print(f"{indent}  │  {mark} {hop['why']}  "
              f"[tier {hop['warmth_tier']} · {hop['relationship']}]")
        if show_links:
            if hop["evidence_snippet"]:
                print(f"{indent}  │    “{_ellipsis(hop['evidence_snippet'], 110)}”")
            if hop["source_url"]:
                # Never truncate a URL: a clipped link looks valid and is not.
                print(f"{indent}  │    {hop['source_url']}")
        print(f"{indent}  {hop['label']}")



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli",
                                     description="VC warm-intro pathfinder")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_seed = sub.add_parser("seed", help="build Drew's warm first degree")
    p_seed.add_argument("--discover", action="store_true",
                        help="also pull human-hosted VC podcasts from Apple's directory")

    p_connect = sub.add_parser("connect", help="find warm-intro paths")
    p_connect.add_argument("person_a")
    p_connect.add_argument("person_b")
    p_connect.add_argument("--depth", type=int, default=config.CONNECT_DEPTH)
    p_connect.add_argument("--weak", action="store_true",
                           help="also traverse the opt-in weak co-occurrence "
                                "(co_mention) tier — short paths, labelled weak")

    p_disc = sub.add_parser("discover", help="warmest reachable people")
    p_disc.add_argument("person")
    p_disc.add_argument("--limit", type=int, default=20)



    args = parser.parse_args(argv)
    init_db()
    db = SessionLocal()
    try:
        if args.cmd == "seed":
            result = seed_drew(db, progress=_progress, discover=args.discover)
            print(result)

        elif args.cmd == "connect":
            _print_paths(connect_people(db, args.person_a, args.person_b,
                                        depth=args.depth, progress=_progress,
                                        include_weak=args.weak))

        elif args.cmd == "discover":
            result = discover(db, args.person, limit=args.limit)
            if not result.get("found"):
                print(result.get("reason"))
                return 1
            print(f"\nWarmest people reachable from {result['person']}:\n")
            for person in result["neighborhood"]:
                warm = " ★" if person["is_warm"] else ""
                print(f"  {person['warmth_score']:>5}  {person['hops']}h  "
                      f"{person['label']}{warm}  — {person['via']}")
            print()

        elif args.cmd == "stats":
            from sqlalchemy import func, select

            from .models import Organization, Person, RelationshipEdge
            from .providers.stats import STATS
            for model, label in ((Person, "people"), (Organization, "orgs"),
                                 (RelationshipEdge, "edges")):
                print(f"  {label:8} {db.scalar(select(func.count()).select_from(model))}")
            print(f"  providers {STATS.snapshot()}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
