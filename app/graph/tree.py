"""Build a person's network TREE, and compare two of them.

`build_tree` mirrors ArtemisV2's expansion snapshot (nodes + edges + summary),
with one substitution: it ranks by WARMTH rather than by a confidence score.
Every node hangs off the warmest path that reaches it, so the tree is the
Dijkstra shortest-path tree over edge cost — the parent of a node is the person
through whom you would actually be introduced.

`compare_trees` answers the question the old engine could not: *how does this
person's network relate to Drew's?* It reports the people both can reach, ranked
by how cheap the introduction is from BOTH sides, which is precisely the set of
mutual contacts worth asking.
"""
from __future__ import annotations

import heapq
import itertools
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..edges import taxonomy
from ..models import Organization, Person, RelationshipEdge, Source
from .resolve import resolve_person
from .connect import _adjacency
from .enrich import get_enricher


def _lookup(db: Session, name: str) -> Optional[Person]:
    """Same resolver the pathfinder uses; the tree must not disagree with it
    about who exists."""
    return resolve_person(db, name)


def _dijkstra(adj, root_id: str, max_hops: int, banned: Optional[set] = None):
    """Warmest-path tree from `root_id`, never routing through `banned`.

    Returns (cost, hops, parent) keyed by person id. `parent[x] = (parent_id,
    edge)` is the warmest way to reach x, i.e. who introduces you.

    `banned` matters when comparing two people: a contact Sheel can reach only
    by going *through Drew* is not a contact they share — it is simply Drew's.
    """
    banned = banned or set()
    counter = itertools.count()
    cost: Dict[str, float] = {root_id: 0.0}
    hops: Dict[str, int] = {root_id: 0}
    parent: Dict[str, Tuple[str, RelationshipEdge]] = {}
    heap = [(0.0, 0, next(counter), root_id)]

    while heap:
        current, hop, _tie, node = heapq.heappop(heap)
        if current > cost.get(node, float("inf")):
            continue
        if hop >= max_hops or (node in banned and node != root_id):
            continue  # a banned node may be reached, never traversed
        for neighbor, edge in adj.get(node, []):
            new_cost = current + taxonomy.edge_cost(edge.relationship_type)
            if new_cost < cost.get(neighbor, float("inf")):
                cost[neighbor] = new_cost
                hops[neighbor] = hop + 1
                parent[neighbor] = (node, edge)
                heapq.heappush(heap, (new_cost, hop + 1, next(counter), neighbor))
    return cost, hops, parent


def _ensure(db: Session, name: str, depth: int, progress=None) -> Optional[Person]:
    """Resolve a person, enriching only when we hold nothing on them yet."""
    person = _lookup(db, name)
    if person is None or not person.enriched:
        get_enricher().enrich_neighborhood(db, name, depth=depth, progress=progress)
        person = _lookup(db, name)
    return person


def _node_payload(person: Person, cost: float, hops: int,
                  edge: Optional[RelationshipEdge],
                  src_by_id) -> dict:
    node = {
        "id": person.id,
        "label": person.canonical_name,
        "hops": hops,
        "total_cost": round(cost, 2),
        "warmth_score": taxonomy.warmth_score(cost, hops),
        "is_warm": bool(person.is_warm),
    }
    if edge is not None:
        source = src_by_id.get(edge.source_id)
        node.update({
            "relationship_from_parent": edge.relationship_type,
            "warmth_tier": taxonomy.warmth_tier(edge.relationship_type),
            "why": taxonomy.label_for(edge.relationship_type),
            "evidence_snippet": edge.evidence_snippet or "",
            "source_url": source.url if source else "",
        })
    return node


def build_tree(db: Session, name: str, depth: int = None, max_hops: int = None,
               progress=None) -> dict:
    """The warmest-path tree rooted at `name`, plus an ArtemisV2-style summary."""
    depth = depth or config.CONNECT_DEPTH
    # A tree is for reading, so it keeps a display bound even when pathfinding
    # is unlimited; pass max_hops=0 for the whole reachable set.
    max_hops = config.hop_limit(max_hops or 0) if max_hops is not None \
        else config.hop_limit()

    root = _ensure(db, name, depth, progress=progress)
    if root is None:
        return {"found": False,
                "reason": f"'{name}' is not in the graph — no structured source "
                          f"places them in the VC/startup network."}

    adj, person_by_id, src_by_id, _pen = _adjacency(db)
    cost, hops, parent = _dijkstra(adj, root.id, max_hops)

    # Children of each parent, warmest first: this is the introduction chain.
    children: Dict[str, List[str]] = defaultdict(list)
    for pid, (parent_id, _edge) in parent.items():
        children[parent_id].append(pid)
    for pid in children:
        children[pid].sort(key=lambda x: cost[x])

    def _subtree(pid: str) -> dict:
        edge = parent[pid][1] if pid in parent else None
        node = _node_payload(person_by_id[pid], cost[pid], hops[pid], edge,
                             src_by_id)
        kids = [_subtree(c) for c in children.get(pid, [])]
        if kids:
            node["children"] = kids
        return node

    reachable = [pid for pid in cost if pid != root.id and pid in parent]
    tiers = Counter(taxonomy.warmth_tier(parent[pid][1].relationship_type) for pid in reachable)
    by_hop = Counter(hops[pid] for pid in reachable)

    # Hubs: who introduces the most people in THIS tree.
    hub_counts = Counter(parent[pid][0] for pid in reachable)
    hubs = [{"label": person_by_id[pid].canonical_name,
             "introduces": n,
             "hops": hops.get(pid, 0)}
            for pid, n in hub_counts.most_common(10)]

    org_ids = {e.organization_id
               for pid in reachable
               for e in [parent[pid][1]] if e.organization_id}
    orgs = list(db.execute(
        select(Organization).where(Organization.id.in_(org_ids))).scalars()) \
        if org_ids else []

    return {
        "found": True,
        "person": root.canonical_name,
        "reachable": len(reachable),
        # None when unlimited: `Infinity` is not valid JSON.
        "max_hops": None if max_hops == float("inf") else int(max_hops),
        "by_hop": dict(sorted(by_hop.items())),
        "by_tier": dict(sorted(tiers.items())),
        "hubs": hubs,
        "organizations": sorted(o.name for o in orgs),
        "tree": _subtree(root.id),
    }


def _chain_avoids(pid: str, parent, banned: set) -> bool:
    """True when the warmest route to `pid` never passes through a banned node."""
    while pid in parent:
        parent_id, _edge = parent[pid]
        if parent_id in banned:
            return False
        pid = parent_id
    return True


def _chain(pid: str, parent, person_by_id, src_by_id=None) -> List[dict]:
    """The introduction chain from the root down to `pid` (root excluded).

    Each hop carries WHY it exists and WHERE that came from — a bare list of
    names is unusable for actually asking for the intro.
    """
    src_by_id = src_by_id or {}
    hops = []
    while pid in parent:
        parent_id, edge = parent[pid]
        source = src_by_id.get(edge.source_id)
        hops.append({
            "label": person_by_id[pid].canonical_name,
            "from": person_by_id[parent_id].canonical_name,
            "relationship": edge.relationship_type,
            "warmth_tier": taxonomy.warmth_tier(edge.relationship_type),
            "why": taxonomy.label_for(edge.relationship_type),
            "evidence_snippet": edge.evidence_snippet or "",
            "source_url": source.url if source else "",
        })
        pid = parent_id
    return list(reversed(hops))


def compare_trees(db: Session, name_a: str, name_b: str, depth: int = None,
                  radius: int = None, limit: int = 20,
                  progress=None) -> dict:
    """How `name_b`'s network relates to `name_a`'s, within `radius` hops.

    `radius` is a person's NETWORK, not everyone they can eventually reach.
    Comparing full 5-hop reachability is meaningless: inside one connected
    component everybody reaches everybody, and the overlap is always 100%. Two
    hops is the set of people you could plausibly be introduced to.

    Neither person is allowed to route through the other. A contact Sheel can
    reach only via Drew belongs to Drew, not to the pair of them.
    """
    depth = depth or config.CONNECT_DEPTH
    radius = radius or config.COMPARE_RADIUS

    if progress:
        progress(f"[1/2] mapping {name_a}…")
    a = _ensure(db, name_a, depth, progress=progress)
    if progress:
        progress(f"[2/2] mapping {name_b}…")
    b = _ensure(db, name_b, depth, progress=progress)

    if a is None or b is None:
        missing = name_a if a is None else name_b
        return {"found": False,
                "reason": f"'{missing}' is not in the graph — no structured "
                          f"source places them in the VC/startup network."}
    if a.id == b.id:
        return {"found": False, "reason": "those are the same person"}

    adj, people, srcs, _pen = _adjacency(db)
    cost_a, hops_a, parent_a = _dijkstra(adj, a.id, radius, banned={b.id})
    cost_b, hops_b, parent_b = _dijkstra(adj, b.id, radius, banned={a.id})

    # Distance between them is a property of the whole graph, not the radius.
    _c, hops_full, _p = _dijkstra(adj, a.id, config.hop_limit())

    set_a = {p for p in cost_a if p not in (a.id, b.id)
             and _chain_avoids(p, parent_a, {b.id})}
    set_b = {p for p in cost_b if p not in (a.id, b.id)
             and _chain_avoids(p, parent_b, {a.id})}
    shared = set_a & set_b

    mutual = []
    for pid in shared:
        combined = cost_a[pid] + cost_b[pid]
        mutual.append({
            "label": people[pid].canonical_name,
            "is_warm": bool(people[pid].is_warm),
            "hops_from_a": hops_a[pid],
            "hops_from_b": hops_b[pid],
            "combined_cost": round(combined, 2),
            "introduction_score": taxonomy.warmth_score(
                combined, hops_a[pid] + hops_b[pid]),
            "chain_from_a": _chain(pid, parent_a, people, srcs),
            "chain_from_b": _chain(pid, parent_b, people, srcs),
        })
    mutual.sort(key=lambda m: (m["combined_cost"],
                               m["hops_from_a"] + m["hops_from_b"]))

    union = set_a | set_b
    return {
        "found": True,
        "person_a": a.canonical_name,
        "person_b": b.canonical_name,
        "radius": radius,
        "reach_a": len(set_a),
        "reach_b": len(set_b),
        "shared": len(shared),
        # Jaccard: how much of their combined networks they share.
        "overlap_pct": round(100 * len(shared) / len(union), 1) if union else 0.0,
        "only_a": len(set_a - shared),
        "only_b": len(set_b - shared),
        "directly_connected": b.id in hops_full,
        "hops_between": hops_full.get(b.id),
        "mutual_contacts": mutual[:limit],
    }
