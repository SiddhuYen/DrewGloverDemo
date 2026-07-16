"""Resumable ROI-ranked enrichment of Drew's currently reachable network.

The first run snapshots every structurally reachable ``enriched=0`` person to
JSON. Later runs consume that fixed queue, so enrichment discovering new nodes
does not turn a finite audit into an endlessly growing crawl.

    ./.venv/bin/python scripts/enrich_unenriched.py --build
    ./.venv/bin/python scripts/enrich_unenriched.py --limit 25 --budget 600
    ./.venv/bin/python scripts/enrich_unenriched.py --status

Priority combines proximity to Drew, existing structural degree, exact
Wikidata identity, warm/direct status, and name quality. The graph database is
the durable completion checkpoint: successfully attempted people are marked
``enriched=1`` by the normal Enricher, so interrupted runs resume safely.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.edges.names import is_noise_name, person_norm_key  # noqa: E402
from app.graph.connect import _adjacency  # noqa: E402
from app.graph.enrich import get_enricher  # noqa: E402
from app.models import Person  # noqa: E402

DEFAULT_QUEUE = Path("data/drew_unenriched_queue.json")


def _distances(db, root: Person):
    adj, people, _sources, _penalties = _adjacency(db)
    def walk(professional_only=False):
        distance = {root.id: 0}
        q = deque([root.id])
        while q:
            node = q.popleft()
            for neighbor, edge in adj.get(node, []):
                if professional_only and edge.relationship_type == "co_star":
                    continue
                if neighbor not in distance:
                    distance[neighbor] = distance[node] + 1
                    q.append(neighbor)
        return distance
    return walk(False), walk(True), adj, people


def _noisy(name: str) -> bool:
    """Display-name/brand signals that make blind name searches low ROI."""
    words = name.split()
    return bool(
        is_noise_name(name)
        or len(words) < 2
        or len(words) > 6
        or any(ch in name for ch in "★🌻🎀💗😍🪄🧿🇵🇸|@")
        or re.search(r"\b(official|art collection|legal|wellness|coach|cakes|"
                     r"painting|realtor|barber|progress|alumni|dads|eats|bear|"
                     r"studio|media|ventures|capital|fund|company|accounting|"
                     r"manager|information security|reasoned choice|think medium|"
                     r"department|employment|careers?|jobs?|podcast|newsletter|"
                     r"community|network|foundation|association|university|"
                     r"close menu|open menu|navigation|read more|learn more|"
                     r"sign in|log in|subscribe|contact us)\b", name, re.I)
    )


def _priority(person: Person, depth: int, degree: int) -> tuple:
    # Lower sorts first. Exact identities and existing bridge degree dominate
    # raw proximity: an identified two-hop founder is much higher ROI than an
    # ambiguous one-edge Instagram display name.
    score = (depth * 20 - min(degree, 30) * 10
             - (150 if person.wikidata_qid else 0)
             - (50 if person.is_warm else 0)
             + (1000 if _noisy(person.canonical_name) else 0))
    return score, depth, -degree, person.canonical_name.casefold()


def build_queue(db, path: Path, seed: str) -> list:
    root = db.scalar(select(Person).where(Person.norm_name == person_norm_key(seed)))
    if root is None:
        raise SystemExit(f"seed {seed!r} is not in the graph")
    distances, professional_distances, adj, people = _distances(db, root)
    rows = []
    for pid, depth in distances.items():
        person = people.get(pid)
        if person is None or person.enriched or pid == root.id:
            continue
        professional_degree = sum(
            edge.relationship_type != "co_star" for _neighbor, edge in adj.get(pid, []))
        # Co-star-only branches remain exhaustive but sort behind every person
        # reachable through an actionable professional/social chain.
        roi_depth = professional_distances.get(pid, depth + 20)
        rows.append({
            "id": pid, "name": person.canonical_name, "depth": depth,
            "roi_depth": roi_depth, "degree": professional_degree,
            "qid": person.wikidata_qid or "",
            "warm": bool(person.is_warm), "noisy": _noisy(person.canonical_name),
            "priority": _priority(person, roi_depth, professional_degree)[0],
        })
    rows.sort(key=lambda r: (r["priority"], r["depth"], -r["degree"],
                             r["name"].casefold()))
    payload = {"seed": seed, "created_at": time.time(), "count": len(rows),
               "people": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return rows


def load_queue(path: Path) -> list:
    if not path.exists():
        raise SystemExit(f"queue missing: run with --build first ({path})")
    return json.loads(path.read_text())["people"]


def rerank_queue(db, path: Path) -> list:
    payload = json.loads(path.read_text())
    people = {p.id: p for p in db.scalars(select(Person)).all()}
    rows = []
    for row in payload["people"]:
        person = people.get(row["id"])
        if person is None:
            continue
        row["qid"] = person.wikidata_qid or ""
        row["warm"] = bool(person.is_warm)
        row["noisy"] = _noisy(person.canonical_name)
        row["priority"] = _priority(
            person, row.get("roi_depth", row["depth"]), row["degree"])[0]
        rows.append(row)
    rows.sort(key=lambda r: (r["priority"], r.get("roi_depth", r["depth"]),
                             -r["degree"], r["name"].casefold()))
    payload["people"] = rows
    payload["ranking_updated_at"] = time.time()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    ap.add_argument("--seed", default=config.DEMO_SEED_NAME)
    ap.add_argument("--build", action="store_true", help="rebuild fixed snapshot")
    ap.add_argument("--rerank", action="store_true",
                    help="re-score the existing fixed snapshot")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--top", type=int, default=0,
                    help="operate on only the top N queue entries")
    ap.add_argument("--limit", type=int, default=25,
                    help="maximum people attempted this run; 0 means unlimited")
    ap.add_argument("--budget", type=float, default=600,
                    help="wall-clock seconds; 0 means unlimited")
    args = ap.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        if args.build:
            queue = build_queue(db, args.queue, args.seed)
        elif args.rerank:
            queue = rerank_queue(db, args.queue)
        else:
            queue = load_queue(args.queue)
        if args.top:
            queue = queue[:args.top]
        ids = [r["id"] for r in queue]
        done = set(db.scalars(select(Person.id).where(
            Person.id.in_(ids), Person.enriched == 1)).all()) if ids else set()
        pending = [r for r in queue if r["id"] not in done]
        print(f"queue={len(queue)}  complete={len(done)}  pending={len(pending)}")
        if args.status or not pending:
            return 0

        enricher = get_enricher()
        deadline = time.monotonic() + args.budget if args.budget else float("inf")
        attempted = 0
        for row in pending:
            if (args.limit and attempted >= args.limit) or time.monotonic() >= deadline:
                break
            print(f"\n[{len(done) + attempted + 1}/{len(queue)}] "
                  f"d={row['depth']} deg={row['degree']} score={row['priority']} "
                  f"{row['name']}", flush=True)
            enricher.enrich_person(db, row["name"], progress=lambda m: print(m, flush=True))
            attempted += 1
        completed = len(set(db.scalars(select(Person.id).where(
            Person.id.in_(ids), Person.enriched == 1)).all())) if ids else 0
        print(f"\nattempted={attempted}  complete={completed}  "
              f"pending={len(queue) - completed}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
