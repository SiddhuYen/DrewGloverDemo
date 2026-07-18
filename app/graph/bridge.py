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
                   opposite_component: Optional[Set[str]] = None,
                   prefer_notable: bool = False) -> tuple:
    """Sort key for the expansion frontier. LOWER sorts first (expanded sooner).

    Order of precedence:
      1. a confirmed meeting point (in the other side's reachable set) — first;
      2. reached through a PODCAST — a host who interviewed the target has
         interviewed many others, so enriching them fans out toward the media
         world. This OVERRIDES the fame direction: Joe Rogan and Theo Von are
         famous, but they are precisely the bridges;
      3. the FAME DIRECTION — normally the gradient runs DOWN (less notable
         first, walking toward ordinary reachable networks). With
         `prefer_notable` it runs UP: expand the MOST notable, highest-degree
         people first, to map the seed's famous / out-of-domain network. This is
         the "push up" mode used to build Drew's famous backbone.
    """
    meets = bool(opposite_component and person.id in opposite_component)
    via_podcast = arrival_rel in _PODCAST_RELS
    notable = is_notable(db, person)
    deg = _degree(db, person)
    warm_key = 1                           # neutral unless walking down (below)
    if prefer_notable:
        fame_key = 0 if notable else 1     # famous first
        deg_key = -deg                     # hubs first
    else:
        fame_key = 1 if notable else 0     # famous last (walk down)
        deg_key = deg                      # leaves first
        # The seed's OWN first degree — seeded warm contacts and imported
        # LinkedIn connections — is the warmest possible bridge. Enrich it before
        # equally-ordinary strangers so a path through the user's real network is
        # found inside the fanout budget, instead of being crowded out. This is
        # what makes deep search compose with an uploaded LinkedIn export: the
        # people you actually know are explored first.
        warm_key = 0 if getattr(person, "is_warm", False) else 1
    return (0 if meets else 1, 0 if via_podcast else 1,
            warm_key, fame_key, deg_key)


def rank_frontier(db: Session, candidates, *,
                  opposite_component: Optional[Set[str]] = None,
                  prefer_notable: bool = False) -> List[Person]:
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
                              opposite_component=opposite_component,
                              prefer_notable=prefer_notable)
        if person.id not in best or rank < best[person.id][0]:
            best[person.id] = (rank, person)
    return [person for _rank, person in sorted(best.values(), key=lambda x: x[0])]
