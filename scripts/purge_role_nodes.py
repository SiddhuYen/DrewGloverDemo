"""Delete person nodes that are job descriptions, not humans.

A roster page renders "Executive Assistant" in the same text node shape as a
partner's name. Before `is_noise_name` learned the all-role-token rule, such a
string became a Person, and because dedup is by normalised name, the SAME node
was reused for every firm that listed the role — merging four unrelated firms
into one false bridge.

    Executive Assistant   57 edges across Foundry, Wing, Uncork, Framework

Deleting the node deletes those edges. Every one of them was fabricated: no
source ever asserted that two people were colleagues *because both firms employ
an executive assistant*.

    ./.venv/bin/python scripts/purge_role_nodes.py --dry-run
    ./.venv/bin/python scripts/purge_role_nodes.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, or_, select          # noqa: E402

from app.db import SessionLocal, init_db            # noqa: E402
from app.edges.names import is_noise_name           # noqa: E402
from app.models import Person, RelationshipEdge     # noqa: E402


def find_role_nodes(db):
    """Person rows whose name carries no name-bearing token.

    A node with a Wikidata QID was identified authoritatively and is never a
    role title, so it is exempt — belt and braces against a surname like
    "Fellow" or a real person named "Chief".
    """
    return [p for p in db.execute(select(Person)).scalars()
            if not p.wikidata_qid and is_noise_name(p.canonical_name)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="delete role-title person nodes")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be deleted, change nothing")
    args = parser.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        doomed = find_role_nodes(db)
        if not doomed:
            print("no role-title person nodes found")
            return 0

        total_edges = 0
        for person in doomed:
            edges = db.execute(select(RelationshipEdge).where(or_(
                RelationshipEdge.person_a_id == person.id,
                RelationshipEdge.person_b_id == person.id,
            ))).scalars().all()
            total_edges += len(edges)
            print(f"  {person.canonical_name:34} {len(edges):3} edges")

        print(f"\n{len(doomed)} phantom nodes, {total_edges} fabricated edges")
        if args.dry_run:
            print("dry run — nothing deleted")
            return 0

        ids = [p.id for p in doomed]
        db.execute(delete(RelationshipEdge).where(or_(
            RelationshipEdge.person_a_id.in_(ids),
            RelationshipEdge.person_b_id.in_(ids),
        )))
        db.execute(delete(Person).where(Person.id.in_(ids)))
        db.commit()
        print(f"deleted {len(doomed)} nodes and {total_edges} edges")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
