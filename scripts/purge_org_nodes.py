"""Delete person nodes that are actually ORGANISATIONS, not humans.

Grammar cannot separate a brand from a person — "Andreessen Horowitz",
"Think Medium" and "Armchair Umbrella" (Dax Shepard's production company) are
each two proper nouns, exactly like a personal name. Yet as person nodes they
become false HUBS: "Andreessen Horowitz" (deg 82) silently bridges everyone a16z
is associated with, and "Armchair Umbrella" fused every guest of the show into a
fake "Bill Gates knows Monica Lewinsky" path. None of those ties was ever
structurally asserted between two PEOPLE — the org is standing in for a person.

Two independent signals, so a real person with a messy scraped name (an all-caps
handle, a "| bio" suffix) is never deleted:

  1. name-shape  — an org/legal/media marker token ("... Ventures", "... Team",
     "... LLC", "Armchair Umbrella") or a job title glued into the interior
     ("Xbox Co-Founder Ed Fries"). This is `is_noise_name`'s org rule.
  2. Wikidata    — for a marker-less node that is still a HUB (degree >= 2), the
     name is resolved on Wikidata; if it maps to a NON-human entity (a company,
     a VC firm), it is an org. This is what catches "Andreessen Horowitz".

A node carrying a Wikidata QID was identified authoritatively as a human and is
always exempt.

    ./.venv/bin/python scripts/purge_org_nodes.py --dry-run
    ./.venv/bin/python scripts/purge_org_nodes.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, or_, select          # noqa: E402

from app.db import SessionLocal, init_db            # noqa: E402
from app.edges.names import (                        # noqa: E402
    ORG_SUFFIXES, _EMBEDDED_TITLE, _ROLE_AFFIXES_JOINED,
    normalize, strip_role_affixes)
from app.graph.connect import _adjacency            # noqa: E402
from app.graph.enrich import get_enricher           # noqa: E402
from app.models import Person, RelationshipEdge     # noqa: E402

# Below this degree an org-as-person is a harmless leaf (it bridges nobody), so
# it is not worth a Wikidata round-trip. Marker-named orgs are removed at any
# degree; only the marker-LESS Wikidata check is gated on being a hub.
_HUB_DEGREE = 2

# The Wikidata name-resolver returns a best-match entity, which for a COMMON
# human name is often a non-human namesake — a film, a fictional character, a
# ship, an album. "David Rosenthal" (a real podcast host, 72 real edges) resolves
# to a non-human QID; deleting on "not is_human" alone would wipe him out. So a
# marker-less node is only an org if the resolved entity is specifically an
# ORGANISATION type. Precision over recall: we would rather miss a film-as-person
# node than delete a real person.
_ORG_LIKE_WORDS = {"business", "enterprise", "company", "corporation", "firm",
                   "organization", "organisation", "agency", "media", "brand",
                   "podcast", "magazine", "publisher", "network", "holding",
                   "startup", "bank", "conglomerate", "studio", "label",
                   "nonprofit", "newspaper", "broadcaster"}


def _resolved_kind_is_org(enr, qid: str) -> bool:
    """True only if the resolved entity is an ORGANISATION. A 'disambiguation
    page' means several real HUMANS share the name — the opposite of an org — so
    it fails closed. Words are matched whole ('media' must not match the
    'wikimedia' in 'wikimedia human name disambiguation page')."""
    kinds = enr.wikidata.org_kinds(qid)
    if any("disambiguation" in kind for kind in kinds):
        return False
    words = {w for kind in kinds for w in kind.split()}
    return bool(words & _ORG_LIKE_WORDS)


def _has_org_marker(name: str) -> bool:
    tokens = normalize(name).split()
    if any(t in ORG_SUFFIXES and t != "co" for t in tokens):
        return True
    resid = normalize(strip_role_affixes(name)).split()
    return len(resid) >= 3 and any(t in _ROLE_AFFIXES_JOINED or t in _EMBEDDED_TITLE
                                   for t in resid)


def find_org_nodes(db, progress=None):
    """Return [(person, reason)] for person nodes that are organisations."""
    enr = get_enricher()
    adj, _by, _s, _p = _adjacency(db)
    doomed = []
    for p in db.execute(select(Person)).scalars():
        if p.wikidata_qid:                      # authoritatively a human
            continue
        if _has_org_marker(p.canonical_name):
            doomed.append((p, "org/title marker"))
            continue
        if len(adj.get(p.id, [])) < _HUB_DEGREE:
            continue                            # marker-less leaf: leave it
        # marker-less hub: only an org if the name resolves to an entity that is
        # non-human AND specifically an ORGANISATION type (not a film/character
        # namesake of a real person).
        try:
            qid = enr.wikipedia.qid_for_name(p.canonical_name)
            if (qid and not enr.wikidata.is_human(qid)
                    and _resolved_kind_is_org(enr, qid)):
                kinds = ", ".join(enr.wikidata.org_kinds(qid)[:2])
                doomed.append((p, f"wikidata org {qid} ({kinds})"))
        except Exception as exc:
            if progress:
                progress(f"  wikidata check failed for {p.canonical_name}: {exc}")
    return doomed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="delete org-as-person nodes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        doomed = find_org_nodes(db, progress=lambda m: print(m, file=sys.stderr))
        if not doomed:
            print("no org-as-person nodes found")
            return 0

        adj, _by, _s, _p = _adjacency(db)
        total_edges = 0
        for person, reason in sorted(doomed, key=lambda x: -len(adj.get(x[0].id, []))):
            deg = len(adj.get(person.id, []))
            total_edges += deg
            print(f"  {person.canonical_name[:38]:38} deg{deg:4}  [{reason}]")

        print(f"\n{len(doomed)} org-as-person nodes, {total_edges} fabricated edges")
        if args.dry_run:
            print("dry run — nothing deleted")
            return 0

        ids = [p.id for p, _ in doomed]
        db.execute(delete(RelationshipEdge).where(or_(
            RelationshipEdge.person_a_id.in_(ids),
            RelationshipEdge.person_b_id.in_(ids))))
        db.execute(delete(Person).where(Person.id.in_(ids)))
        db.commit()
        print(f"deleted {len(doomed)} nodes and their edges")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
