"""Strip EDGAR namesake board memberships — fabricated corporate boards.

The EDGAR silo matches a person to SEC Form-4 filings BY NAME. For a distinctive
name that is genuinely the filer (Elon Musk -> Tesla) the co-insiders it links
are a real board. For a COMMON name it is a namesake: the podcast host
"David Rosenthal" was grafted onto a stranger's board of 11 corporate insiders;
"Michael Scott", "John Murphy", "Craig Robinson" likewise.

The signal that separates them is identity confirmation. A `board_member` edge
sourced from EDGAR is trustworthy only when it is ANCHORED to a person we
identified authoritatively — a Wikidata QID. So:

  keep    an EDGAR board edge if EITHER endpoint has a QID
          (Musk<->Kirkhorn survives; the famous, plausible board stays)
  remove  an EDGAR board edge when BOTH endpoints are unconfirmed (no QID)
          (David Rosenthal's phantom board, and internal no-QID meshes, go)

Only EDGAR-sourced (`structured`) board edges are touched. Wikidata (P463) and
ProPublica board edges carry their own identity guards and are left alone.

    ./.venv/bin/python scripts/purge_namesake_boards.py --dry-run
    ./.venv/bin/python scripts/purge_namesake_boards.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select                # noqa: E402

from app.db import SessionLocal, init_db            # noqa: E402
from app.models import Person, RelationshipEdge, Source  # noqa: E402


def find_namesake_board_edges(db):
    """EDGAR board edges with NO QID-confirmed endpoint. Returns (edges, sample)."""
    # provider per source id, cached so we don't re-query
    provider = {s.id: s.provider for s in db.execute(select(Source)).scalars()}
    qid = {p.id: p.wikidata_qid for p in db.execute(select(Person)).scalars()}
    doomed, sample = [], {}
    for e in db.execute(select(RelationshipEdge).where(
            RelationshipEdge.relationship_type == "board_member")).scalars():
        if provider.get(e.source_id) != "structured":
            continue                              # only EDGAR name-matches
        if qid.get(e.person_a_id) or qid.get(e.person_b_id):
            continue                              # anchored to a confirmed person
        doomed.append(e.id)
        for pid in (e.person_a_id, e.person_b_id):
            sample[pid] = sample.get(pid, 0) + 1
    return doomed, sample


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="strip EDGAR namesake board edges")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        doomed, sample = find_namesake_board_edges(db)
        if not doomed:
            print("no unconfirmed EDGAR board edges found")
            return 0
        name = {p.id: p.canonical_name
                for p in db.execute(select(Person)).scalars()}
        print(f"{len(doomed)} EDGAR board edges with no QID-confirmed endpoint")
        print("most-affected nodes (their phantom boards will thin/vanish):")
        for pid, n in sorted(sample.items(), key=lambda x: -x[1])[:20]:
            print(f"   {name.get(pid, '?')[:32]:32} {n} edges")
        if args.dry_run:
            print("dry run — nothing deleted")
            return 0
        db.execute(delete(RelationshipEdge).where(
            RelationshipEdge.id.in_(doomed)))
        db.commit()
        print(f"\ndeleted {len(doomed)} namesake board edges")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
