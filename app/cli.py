"""CLI: seed, connect, discover, tree, compare, stats.

    python -m app.cli seed --discover
    python -m app.cli connect  "Drew Glover" "Charles Hudson"
    python -m app.cli discover "Drew Glover"
    python -m app.cli tree     "Sheel Mohnot" --max-hops 2
    python -m app.cli compare  "Sheel Mohnot"          # against Drew
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .db import SessionLocal, init_db
from .graph.connect import connect_people, discover
from .graph.tree import build_tree, compare_trees
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


def _print_tree(node: dict, max_depth: int, prefix: str = "",
                is_last: bool = True, depth: int = 0,
                show_links: bool = False) -> None:
    if depth == 0:
        star = " ★" if node.get("is_warm") else ""
        print(f"{node['label']}{star}")
    else:
        mark = _TIER_MARK.get(node.get("warmth_tier", 5), "○")
        star = " ★" if node.get("is_warm") else ""
        branch = "└── " if is_last else "├── "
        print(f"{prefix}{branch}{mark} {node['label']}{star}"
              f"  [{node.get('why', '')}]")
        if show_links:
            pad = prefix + ("    " if is_last else "│   ")
            if node.get("evidence_snippet"):
                print(f"{pad}      “{_ellipsis(node['evidence_snippet'], 104)}”")
            if node.get("source_url"):
                print(f"{pad}      {node['source_url']}")
    if depth >= max_depth:
        return
    kids = node.get("children") or []
    child_prefix = prefix + ("    " if is_last or depth == 0 else "│   ")
    if depth == 0:
        child_prefix = ""
    for i, child in enumerate(kids):
        _print_tree(child, max_depth, child_prefix, i == len(kids) - 1,
                    depth + 1, show_links)


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


def _print_compare(r: dict, show_links: bool = True) -> None:
    a, b = r["person_a"], r["person_b"]
    print(f"\n{a}  vs  {b}\n")
    print(f"  {a} reaches   {r['reach_a']:>4}")
    print(f"  {b} reaches   {r['reach_b']:>4}")
    print(f"  shared          {r['shared']:>4}   ({r['overlap_pct']}% of the "
          f"combined network)")
    print(f"  only {a:<12} {r['only_a']:>4}")
    print(f"  only {b:<12} {r['only_b']:>4}")
    if r["directly_connected"]:
        print(f"\n  They are connected: {r['hops_between']} hops apart.")
    else:
        print("\n  No structural chain connects them.")

    if not r["mutual_contacts"]:
        print("\n  No mutual contacts.\n")
        return
    print("\n  Best mutual contacts (cheapest introduction from both sides):")
    for m in r["mutual_contacts"]:
        star = " ★" if m["is_warm"] else ""
        print(f"\n  ── {m['label']}{star}   score {m['introduction_score']} "
              f"({m['hops_from_a']}h from {a}, {m['hops_from_b']}h from {b})")
        print(f"\n     via {a}:")
        _print_chain(a, m["chain_from_a"], "       ", show_links)
        print(f"\n     via {b}:")
        _print_chain(b, m["chain_from_b"], "       ", show_links)
    print()


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

    p_tree = sub.add_parser("tree", help="warmest-path network tree for a person")
    p_tree.add_argument("person")
    p_tree.add_argument("--depth", type=int, default=config.CONNECT_DEPTH)
    p_tree.add_argument("--max-hops", type=int, default=3,
                        help="0 = no limit (whole reachable set)")
    p_tree.add_argument("--show", type=int, default=3,
                        help="print the tree to this many hops")
    p_tree.add_argument("--links", action="store_true",
                        help="print the evidence and source URL for each hop")

    p_cmp = sub.add_parser("compare", help="compare a person's network to Drew's")
    p_cmp.add_argument("person")
    p_cmp.add_argument("--against", default=config.DEMO_SEED_NAME)
    p_cmp.add_argument("--depth", type=int, default=config.CONNECT_DEPTH)
    p_cmp.add_argument("--radius", type=int, default=config.COMPARE_RADIUS,
                       help="network radius in hops (default 2)")
    p_cmp.add_argument("--limit", type=int, default=12)
    p_cmp.add_argument("--no-links", action="store_true",
                       help="omit the evidence and source URL for each hop")

    sub.add_parser("stats", help="graph + provider counters")

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

        elif args.cmd == "tree":
            result = build_tree(db, args.person, depth=args.depth,
                                max_hops=args.max_hops, progress=_progress)
            if not result.get("found"):
                print(result.get("reason"))
                return 1
            bound = (f"within {result['max_hops']} hops"
                     if result["max_hops"] else "reachable (no hop limit)")
            print(f"\n{result['person']} — {result['reachable']} people {bound}")
            print(f"  by hop:  {result['by_hop']}")
            print(f"  by tier: {result['by_tier']}")
            if result["hubs"]:
                print("\n  who introduces the most people:")
                for hub in result["hubs"][:5]:
                    print(f"    {hub['introduces']:>4}  {hub['label']} "
                          f"({hub['hops']}h)")
            print()
            _print_tree(result["tree"], max_depth=args.show,
                        show_links=args.links)

        elif args.cmd == "compare":
            result = compare_trees(db, args.against, args.person,
                                   depth=args.depth, radius=args.radius,
                                   limit=args.limit, progress=_progress)
            if not result.get("found"):
                print(result.get("reason"))
                return 1
            _print_compare(result, show_links=not args.no_links)

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
