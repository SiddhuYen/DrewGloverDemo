"""Read-only audit for the two ways a false bridge gets into the graph.

1. ROLE-TITLE NODES — a job description scraped as a person's name. Deterministic;
   `purge_role_nodes.py` removes them.

2. CROSS-FIRM MERGES — a node without a Wikidata QID that appears on the rosters
   of many distinct firms. Dedup is by normalised name, so any string a lot of
   firms print becomes one node bridging them all.

   This is a MONITOR, not a mutation. Real people belong to two or three firms
   (Sheel Mohnot is at BTV and 500 Startups), so an automatic cap would delete
   real serial operators. A human reviews the flags.

    ./.venv/bin/python scripts/audit_merges.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select                              # noqa: E402

from app import config                                          # noqa: E402
from app.db import SessionLocal, init_db                        # noqa: E402
from app.edges.names import is_noise_name                       # noqa: E402
from app.models import Organization, Person, RelationshipEdge   # noqa: E402


def _firms_of(db, person) -> Counter:
    edges = db.execute(select(RelationshipEdge).where(or_(
        RelationshipEdge.person_a_id == person.id,
        RelationshipEdge.person_b_id == person.id,
    ))).scalars()
    names = Counter()
    for edge in edges:
        if not edge.organization_id:
            continue
        org = db.get(Organization, edge.organization_id)
        if org is not None and org.type == "firm":
            names[org.name] += 1
    return names


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        people = list(db.execute(select(Person)).scalars())

        roles = [p for p in people
                 if not p.wikidata_qid and is_noise_name(p.canonical_name)]
        print(f"[1] role-title person nodes: {len(roles)}")
        for p in roles:
            print(f"      {p.canonical_name}")

        print(f"\n[2] unverified people on >= {config.MAX_FIRMS_PER_UNVERIFIED_PERSON} "
              f"distinct firm rosters (review, do not auto-delete):")
        flagged = 0
        for person in people:
            if person.wikidata_qid:
                continue
            firms = _firms_of(db, person)
            if len(firms) >= config.MAX_FIRMS_PER_UNVERIFIED_PERSON:
                flagged += 1
                print(f"      {person.canonical_name:30} {dict(firms)}")
        if not flagged:
            print("      none")

        ok = not roles and not flagged
        print(f"\n{'CLEAN' if ok else 'REVIEW NEEDED'}")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
