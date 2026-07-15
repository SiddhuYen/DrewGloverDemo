"""Resolve a typed name to a person node.

The strict dedup key answers "are these the same person?" — the right question
when deciding whether to merge two nodes, and the wrong one when a user types a
name into a box. A LinkedIn export stores "José Álvarez", "Robert Chen Jr." and
"Sheel Mohnot (BTV)"; people type "Jose Alvarez", "Robert Chen", "Sheel Mohnot".
Measured against a realistic export, six of nine variants failed to resolve, and
the app told the user someone it had just imported was "not in the graph".

Two stages, strict first:

  1. exact `person_norm_key` — an indexed equality, the overwhelming common case
  2. loose `person_search_keys` — a scan, only on a miss

The scan is why this is bounded: it loads names only, and the graph is a few
thousand people. If that stops being true, this wants a stored search column.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..edges.names import person_norm_key, person_search_keys
from ..models import Person


def resolve_person(db: Session, name: str) -> Optional[Person]:
    """The person `name` refers to, or None. Never creates."""
    if not name or not name.strip():
        return None

    norm = person_norm_key(name)
    if norm:
        exact = db.execute(
            select(Person).where(Person.norm_name == norm)
        ).scalars().first()
        if exact is not None:
            return exact

    wanted = person_search_keys(name)
    if not wanted:
        return None

    # Ties are possible ("John Smith" vs "John Andrew Smith"): prefer a node the
    # graph actually knows something about, so a resolve lands on the person with
    # a network rather than an orphan that merely shares a name.
    best = None
    best_rank = (-1, -1)
    for pid, canonical, warm in db.execute(
            select(Person.id, Person.canonical_name, Person.is_warm)).all():
        if not person_search_keys(canonical) & wanted:
            continue
        rank = (1 if warm else 0, 0)
        if rank > best_rank:
            best_rank = rank
            best = pid
        if warm:
            break

    if best is None:
        return None
    return db.get(Person, best)
