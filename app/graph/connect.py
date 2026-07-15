"""Warm-intro pathfinding: expand both endpoints, then meet in the middle.

Every persisted edge is structural (Rule 0 is enforced in builder.add_edge), and
org membership is already collapsed into person-person edges under the Rule 1
cap, so the search runs over a person-only graph and stays simple.

Path cost = sum of edge costs, where cost is a function of warmth tier. Lower is
warmer. Dijkstra finds the warmest route; `_diverse_paths` then re-runs it with
the previous route's bridge nodes excluded, yielding genuinely different intros
rather than three variations on one chain.
"""
from __future__ import annotations

import heapq
import itertools
import math
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..edges import taxonomy
from ..edges.names import person_norm_key
from ..models import Person, RelationshipEdge, Source
from .enrich import get_enricher, target_enrichment_level

Hop = Tuple[str, Optional[RelationshipEdge]]


def _edge_cost(edge: RelationshipEdge) -> float:
    """Pathfinding cost of an edge, derived LIVE from its relationship type.

    Deliberately NOT the stored `edge.cost` column: that was frozen at write
    time, so re-tiering the taxonomy would silently have no effect on existing
    rows. Reading `taxonomy.edge_cost(edge.relationship_type)` makes a tier
    change take effect immediately, with the stored column kept only as
    provenance.
    """
    return taxonomy.edge_cost(edge.relationship_type)


def _adjacency(db: Session, include_weak: bool = False):
    """Undirected person-person adjacency, keeping the WARMEST edge per pair.

    `include_weak` opens the opt-in co-occurrence tier: by default weak
    `co_mention` edges are excluded, so pathfinding stays Rule-0 pure. When True
    (or whenever DEEP_SEARCH is on), they are traversable (at their punishing
    tier-6 cost) so the deep 2-hop web-mined neighbourhood is reachable.

    Also returns `node_penalty`: a per-person routing surcharge that grows with
    degree, so a path avoids transiting a mega-hub (a podcast host with hundreds
    of guests, a firm with many partners) when a lower-degree alternative
    exists. This is what stops every long path from collapsing into a chain
    through the same three interview hubs.
    """
    include_weak = include_weak or config.DEEP_SEARCH
    person_by_id = {p.id: p for p in db.execute(select(Person)).scalars()}
    src_by_id = {s.id: s for s in db.execute(select(Source)).scalars()}

    best: Dict[Tuple[str, str], RelationshipEdge] = {}
    degree: Dict[str, int] = defaultdict(int)
    for edge in db.execute(
        select(RelationshipEdge).where(RelationshipEdge.person_b_id.isnot(None))
    ).scalars():
        a, b = edge.person_a_id, edge.person_b_id
        if not a or not b or a == b:
            continue
        # Defense in depth. add_edge already refuses these, but a hand-edited DB
        # or a future migration must never be able to route a path through one.
        rtype = edge.relationship_type
        if taxonomy.is_weak(rtype):
            if not include_weak:
                continue            # weak co-occurrence tier: opt-in only
        elif not edge.structural or not taxonomy.is_structural(rtype):
            continue
        key = (a, b) if a < b else (b, a)
        current = best.get(key)
        if current is None or _edge_cost(edge) < _edge_cost(current):
            best[key] = edge

    adj: Dict[str, List[Tuple[str, RelationshipEdge]]] = defaultdict(list)
    for (a, b), edge in best.items():
        adj[a].append((b, edge))
        adj[b].append((a, edge))
        degree[a] += 1
        degree[b] += 1

    # Penalise ONLY genuine mega-hubs, and only the EXCESS over the threshold.
    # A flat COEF*ln(deg) charged every node — routing through a normal connector
    # like Bree Hanson (degree 36) cost +2.15, MORE than a whole tier-2 edge, so
    # paths detoured through obscure low-degree nobodies to dodge her. Now a
    # recognisable connector below the threshold pays nothing; only a true funnel
    # (Harry Stebbings, degree 119) pays a mild surcharge to keep every path from
    # collapsing onto the same handful of hubs.
    thr = config.MEGA_HUB_DEGREE
    node_penalty = {
        pid: config.DEGREE_PENALTY_COEF * math.log(deg / thr)
        for pid, deg in degree.items() if deg > thr
    }
    return adj, person_by_id, src_by_id, node_penalty


def _best_path(adj, start: str, target: str, max_hops: int,
               excluded: Optional[Set[str]] = None,
               node_penalty: Optional[Dict[str, float]] = None
               ) -> Optional[List[Hop]]:
    """Lowest-cost (warmest) path, skipping `excluded` intermediate nodes.

    Cost of a step = the edge's tier cost + a routing penalty for the node being
    entered (unless it is the target). The penalty makes a path route around a
    mega-hub when a cheaper way exists, so long paths stop funnelling through the
    same handful of interview hosts.
    """
    excluded = excluded or set()
    node_penalty = node_penalty or {}
    if start == target:
        return [(start, None)]

    # A monotonic counter breaks ties BEFORE heapq ever reaches the path list.
    # Without it, two entries with equal (cost, hops, node) make heapq compare
    # lists of (str, RelationshipEdge) tuples, and comparing two ORM objects
    # raises TypeError. Rare, load-dependent, and fatal — so make it impossible.
    counter = itertools.count()
    best_cost: Dict[str, float] = {start: 0.0}
    heap: List[Tuple[float, int, int, str, List[Hop]]] = [
        (0.0, 0, next(counter), start, [(start, None)])
    ]

    while heap:
        cost, hops, _tie, node, path = heapq.heappop(heap)
        if node == target:
            return path
        if cost > best_cost.get(node, float("inf")):
            continue  # a cheaper route to `node` was already expanded
        if hops >= max_hops:
            continue
        for neighbor, edge in adj.get(node, []):
            if neighbor in excluded and neighbor != target:
                continue
            step = _edge_cost(edge)
            if neighbor != target:
                step += node_penalty.get(neighbor, 0.0)
            new_cost = cost + step
            if new_cost < best_cost.get(neighbor, float("inf")):
                best_cost[neighbor] = new_cost
                heapq.heappush(heap, (new_cost, hops + 1, next(counter),
                                      neighbor, path + [(neighbor, edge)]))
    return None


def _reachable_ids(db: Session, start: str):
    """Every person-id reachable from `start` over the current graph (unbounded).

    Handed to the target-side expansion as a meeting beacon: a target neighbour
    already in this set is a confirmed join and is enriched first.
    """
    adj, _people, _srcs, _pen = _adjacency(db)
    seen = {start}
    frontier = deque([start])
    while frontier:
        node = frontier.popleft()
        for neighbour, _edge in adj.get(node, []):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return seen


def _hop_distance(adj, start: str, target: str) -> Optional[int]:
    """Unbounded shortest hop count, ignoring cost. None when disconnected.

    Used only to explain a failure: "they are 6 hops apart and we cap at 5" is a
    different fact from "no chain of asserted relationships joins them", and a
    user deciding whether to chase an intro needs to know which one they hit.
    """
    if start == target:
        return 0
    seen = {start}
    frontier = deque([(start, 0)])
    while frontier:
        node, hops = frontier.popleft()
        for neighbor, _edge in adj.get(node, []):
            if neighbor == target:
                return hops + 1
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append((neighbor, hops + 1))
    return None


def _diverse_paths(adj, start: str, target: str, max_hops: int,
                   k: int, node_penalty: Optional[Dict[str, float]] = None
                   ) -> List[List[Hop]]:
    """Up to k routes; each avoids every bridge node used by the earlier ones."""
    paths: List[List[Hop]] = []
    excluded: Set[str] = set()
    seen: Set[Tuple[str, ...]] = set()
    for _ in range(k):
        path = _best_path(adj, start, target, max_hops, excluded, node_penalty)
        if path is None:
            break
        signature = tuple(pid for pid, _edge in path)
        if signature in seen:
            break
        seen.add(signature)
        paths.append(path)

        bridges = [pid for pid, _edge in path[1:-1]]
        if not bridges:
            # A direct edge has no bridge to exclude, so re-running would return
            # this very same path k times. One route IS the answer here.
            break
        excluded.update(bridges)
    return paths


def _serialize(path: List[Hop], person_by_id, src_by_id) -> dict:
    nodes, costs, bridges = [], [], []
    for i, (pid, edge) in enumerate(path):
        person = person_by_id.get(pid)
        node = {
            "label": person.canonical_name if person else pid,
            "is_warm": bool(person.is_warm) if person else False,
        }
        if edge is not None:
            costs.append(_edge_cost(edge))
            source = src_by_id.get(edge.source_id)
            node.update({
                "relationship_from_prev": edge.relationship_type,
                "warmth_tier": taxonomy.warmth_tier(edge.relationship_type),
                "why": taxonomy.label_for(edge.relationship_type),
                "evidence_snippet": edge.evidence_snippet or "",
                "source_url": source.url if source else "",
            })
        if 0 < i < len(path) - 1:
            bridges.append(node["label"])
        nodes.append(node)

    hops = len(path) - 1
    total = sum(costs)
    return {
        "hops": hops,
        "total_cost": round(total, 2),
        "warmth_score": taxonomy.warmth_score(total, hops),
        "bridges": bridges,
        "path": nodes,
    }


def _lookup(db: Session, name: str) -> Optional[Person]:
    return db.execute(select(Person).where(
        Person.norm_name == person_norm_key(name))).scalar_one_or_none()


def _try_paths(db: Session, a: Person, b: Person, include_weak: bool = False):
    adj, person_by_id, src_by_id, node_penalty = _adjacency(db, include_weak)
    routes = _diverse_paths(adj, a.id, b.id, config.hop_limit(),
                            config.CONNECT_MAX_PATHS, node_penalty)
    return routes, person_by_id, src_by_id


def connect_people(db: Session, name_a: str, name_b: str,
                   depth: int = None, progress=None,
                   include_weak: bool = False) -> dict:
    """Top-K warmest distinct intro paths from `name_a` to `name_b`.

    Enrichment escalates only as far as it must:

        stage 0  search the graph as it stands            (no network at all)
        stage 1  enrich the two endpoints themselves      (~2 people)
        stage 2  enrich each endpoint's frontier          (up to 2 x fan-out)

    Each stage re-runs the search and returns the moment a path appears. This
    matters: enriching unconditionally cost 3m48s of network to rediscover a
    path that was already in the graph. A target that is already known now
    answers without a single request.
    """
    depth = depth or config.CONNECT_DEPTH
    enricher = get_enricher()

    a, b = _lookup(db, name_a), _lookup(db, name_b)
    if a is not None and b is not None and a.id == b.id:
        return {"connected": False, "reason": "those are the same person"}

    routes: List[List[Hop]] = []
    person_by_id = src_by_id = {}

    # Stage 0 — the graph may already hold a route (pre-crawled backbone).
    if a is not None and b is not None:
        if progress:
            progress("[0] searching the existing graph…")
        routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak)

    # Stage 1 — pull structured sources for the endpoints only.
    if not routes:
        for name in (name_a, name_b):
            person = _lookup(db, name)
            if person is None or person.enriched < target_enrichment_level():
                if progress:
                    progress(f"[1] enriching {name}…")
                if config.DEEP_SEARCH:
                    enricher.enrich_neighborhood(db, name, depth=2, progress=progress)
                else:
                    enricher.enrich_person(db, name, progress=progress)
        a, b = _lookup(db, name_a), _lookup(db, name_b)
        if a is None or b is None:
            missing = name_a if a is None else name_b
            return {"connected": False,
                    "reason": f"'{missing}' is not in the graph — no structured "
                              f"source places them in the VC/startup network."}
        if a.id == b.id:
            return {"connected": False, "reason": "those are the same person"}
        routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak)

    # Stage 2 — widen until the two sides meet, or the budget is spent.
    #
    # The two endpoints are asymmetric. `name_a` is the fixed seed (Drew), whose
    # neighbourhood is already dense; `name_b` is an arbitrary, often cold target
    # sitting on a small island. So we grow the TARGET deeper, walking its
    # frontier down the fame gradient toward ordinary reachable networks, and
    # hand it Drew's current reachable set as a meeting beacon: any target-side
    # node already in Drew's component is expanded first to lock in the join.
    if not routes and depth > 1:
        deadline = time.monotonic() + config.CONNECT_WORK_BUDGET_S
        if progress:
            progress(f"[2] expanding {name_a} (depth {depth})…")
        enricher.enrich_neighborhood(db, name_a, depth=depth, progress=progress,
                                     deadline=deadline)
        routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak)

        if not routes:
            drew_reach = set(_reachable_ids(db, a.id))
            if progress:
                progress(f"[2] expanding {name_b} toward {name_a} "
                         f"(depth {config.CONNECT_TARGET_DEPTH})…")
            enricher.enrich_neighborhood(
                db, name_b, depth=config.CONNECT_TARGET_DEPTH, progress=progress,
                opposite_component=drew_reach, deadline=deadline)
            routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak)

    if not routes:
        # With no hop limit, the only way to fail is genuine disconnection: no
        # chain of asserted relationships joins them. Report the distance anyway
        # when a bound was configured, so a refusal is never ambiguous.
        adj, _pids, _srcs, _pen = _adjacency(db)
        distance = _hop_distance(adj, a.id, b.id)
        if distance is None:
            reason = (f"No chain of structurally-asserted relationships connects "
                      f"{a.canonical_name} to {b.canonical_name} at all. We do "
                      f"not guess at a path from names that merely co-occur.")
        else:
            reason = (f"{a.canonical_name} and {b.canonical_name} are {distance} "
                      f"hops apart, beyond the configured {config.MAX_HOPS}-hop "
                      f"limit. Every hop is real; there are just too many of "
                      f"them. Set VCWI_MAX_HOPS=0 to lift the limit.")
        return {
            "connected": False,
            "person_a": a.canonical_name,
            "person_b": b.canonical_name,
            "hop_distance": distance,
            "reason": reason,
        }

    paths = [_serialize(r, person_by_id, src_by_id) for r in routes]
    paths.sort(key=lambda p: (-p["warmth_score"], p["hops"]))
    best = paths[0]
    return {
        "connected": True,
        "person_a": a.canonical_name,
        "person_b": b.canonical_name,
        "hops": best["hops"],
        "warmth_score": best["warmth_score"],
        "bridges": best["bridges"],
        "path": best["path"],
        "paths": paths,
        "warnings": [
            "Paths are built from structurally-asserted relationships and are "
            "unverified — confirm before requesting an intro.",
            "Coverage is the VC/startup world, not the general public.",
        ],
    }


def discover(db: Session, name: str, limit: int = 20, depth: int = None) -> dict:
    """Warmest reachable people around `name`, by cheapest total path cost."""
    depth = depth or config.CONNECT_DEPTH

    # Only reach for the network when we have nothing on this person yet.
    root = _lookup(db, name)
    if root is None or root.enriched < target_enrichment_level():
        get_enricher().enrich_neighborhood(db, name, depth=depth)
        root = _lookup(db, name)
    if root is None:
        return {"found": False, "reason": f"'{name}' is not in the graph"}

    adj, person_by_id, src_by_id, _pen = _adjacency(db)

    # Dijkstra from the root; keep the cheapest total cost to each person.
    limit = config.hop_limit()
    counter = itertools.count()
    dist: Dict[str, float] = {root.id: 0.0}
    hops_to: Dict[str, int] = {root.id: 0}
    first_edge: Dict[str, RelationshipEdge] = {}
    heap = [(0.0, 0, next(counter), root.id)]
    while heap:
        cost, hops, _t, node = heapq.heappop(heap)
        if cost > dist.get(node, float("inf")) or hops >= limit:
            continue
        for neighbor, edge in adj.get(node, []):
            new_cost = cost + edge.cost
            if new_cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_cost
                hops_to[neighbor] = hops + 1
                first_edge[neighbor] = edge if node == root.id else first_edge.get(node)
                heapq.heappush(heap, (new_cost, hops + 1, next(counter), neighbor))

    people = []
    for pid, cost in sorted(dist.items(), key=lambda kv: kv[1]):
        if pid == root.id:
            continue
        person = person_by_id.get(pid)
        if person is None:
            continue
        edge = first_edge.get(pid)
        source = src_by_id.get(edge.source_id) if edge else None
        people.append({
            "label": person.canonical_name,
            "is_warm": bool(person.is_warm),
            "hops": hops_to.get(pid, 0),
            "total_cost": round(cost, 2),
            "warmth_score": taxonomy.warmth_score(cost, hops_to.get(pid, 1)),
            "via": taxonomy.label_for(edge.relationship_type) if edge else "",
            "source_url": source.url if source else "",
        })
        if len(people) >= limit:
            break

    return {"found": True, "person": root.canonical_name,
            "neighborhood": people, "count": len(people)}
