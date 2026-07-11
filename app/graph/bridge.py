"""Rank an expansion frontier — which unenriched neighbour to enrich next.

Adopted from ArtemisV2's `_ranked_expandable`. Its key, non-obvious insight:
**walk DOWN the fame gradient.** Expand the LEAST famous eligible people first,
because a warm introduction runs toward an ordinary, reachable person's network,
not up into a more-famous one. From Elon Musk you want his non-celebrity Tesla
colleagues, who lead outward toward ordinary professional networks — not Kimbal
Musk, who leads deeper into celebrity-land.

This replaces an earlier `bridge_score` that ranked by Wikidata-QID and
firm-membership signals. That failed on a COLD frontier: those signals are
populated *by* enrichment, so on Musk's twelve unenriched leaf neighbours they
were all blank and every candidate tied. Notability is the opposite — it is
knowable *before* enrichment, with one cached lookup — and it is the signal that
actually matters for reachability.

Two sound ideas are kept from the old scorer: a person already in the OTHER
endpoint's reachable set is a confirmed meeting point (expand it immediately),
and mega-hubs are pushed back so we buy new reach rather than re-expanding a
node everyone already routes through.
"""
from __future__ import annotations

from typing import List, Optional, Set

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Person, RelationshipEdge
from ..providers.wikipedia import WikipediaProvider

_wikipedia: Optional[WikipediaProvider] = None


def _wiki() -> WikipediaProvider:
    global _wikipedia
    if _wikipedia is None:
        _wikipedia = WikipediaProvider()
    return _wikipedia


def _degree(db: Session, person: Person) -> int:
    return db.scalar(
        select(func.count()).select_from(RelationshipEdge).where(
            or_(RelationshipEdge.person_a_id == person.id,
                RelationshipEdge.person_b_id == person.id))
    ) or 0


def is_notable(db: Session, person: Person) -> bool:
    """True if the person has a Wikidata-backed page — i.e. is 'famous'.

    Cheap and enrichment-free: a stored QID answers instantly, else one cached
    Wikipedia title lookup. Used to sort notable people to the BACK of the
    frontier (the fame gradient), never to reject them.
    """
    if person.wikidata_qid:
        return True
    return _wiki().qid_for_name(person.canonical_name) is not None


_PODCAST_RELS = ("podcast_guest", "cohost")


def expansion_rank(db: Session, person: Person,
                   arrival_rel: str = "", *,
                   opposite_component: Optional[Set[str]] = None) -> tuple:
    """Sort key for the expansion frontier. LOWER sorts first (expanded sooner).

    Order of precedence:
      1. a confirmed meeting point (in the other side's reachable set) — first;
      2. reached through a PODCAST — a host who interviewed the target has
         interviewed many others, so enriching them fans out toward the media
         world where ordinary reachable people live. This OVERRIDES the fame
         gradient: Joe Rogan and Theo Von are famous, but they are precisely the
         bridges (the gradient alone would demote them and waste the budget on
         obscure board members who lead nowhere);
      3. the fame gradient — among the rest, the LESS notable the sooner, walking
         toward ordinary networks;
      4. lower current degree — a leaf opens new territory.
    """
    meets = bool(opposite_component and person.id in opposite_component)
    via_podcast = arrival_rel in _PODCAST_RELS
    notable = is_notable(db, person)
    return (0 if meets else 1,
            0 if via_podcast else 1,
            1 if notable else 0,
            _degree(db, person))


def rank_frontier(db: Session, candidates, *,
                  opposite_component: Optional[Set[str]] = None) -> List[Person]:
    """Order an expansion frontier; expand the front of the list first.

    `candidates` is an iterable of either `Person` or `(Person, arrival_edge)`.
    Deduplicates on person id, keeping the best (lowest) rank seen.
    """
    best: dict = {}
    for item in candidates:
        person, edge = item if isinstance(item, tuple) else (item, None)
        if person is None:
            continue
        rel = edge.relationship_type if edge is not None else ""
        rank = expansion_rank(db, person, rel,
                              opposite_component=opposite_component)
        if person.id not in best or rank < best[person.id][0]:
            best[person.id] = (rank, person)
    return [person for _rank, person in sorted(best.values(), key=lambda x: x[0])]
