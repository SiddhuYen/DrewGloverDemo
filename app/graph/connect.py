"""Warm-intro pathfinding: expand both endpoints, then meet in the middle.

Every persisted edge is structural (Rule 0 is enforced in builder.add_edge), and
org membership is already collapsed into person-person edges under the Rule 1
cap, so the search runs over a person-only graph and stays simple.

Path cost = sum of edge costs, where cost is a function of warmth tier. Lower is
warmer. Dijkstra finds the warmest route; `_routes` then finds alternates by
re-running it with ONE earlier bridge removed at a time, yielding genuinely
different intros rather than three variations on one chain.

Two things a warmth tier cannot say, which this module says instead:

  * Whether a bridge would actually relay the intro. `_plausible_first` bans
    famous strangers from the bridge positions outright, so the lead route is
    walkable by construction rather than by hoping a penalty outweighs them.
  * That an alternate route must not be built by demolishing the good one. See
    `_routes`.
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
    """Cost of TRAVERSING an edge = its tier cost + config.HOP_SURCHARGE.

    Deliberately NOT the stored `edge.cost` column: that was frozen at write
    time, so re-tiering the taxonomy would silently have no effect on existing
    rows. Reading `taxonomy.edge_cost(edge.relationship_type)` makes a tier
    change take effect immediately, with the stored column kept only as
    provenance.

    Also deliberately NOT `taxonomy.edge_cost` alone: that is the worth of one
    relationship, which is the right thing to persist on a row but the wrong
    thing to route on. Traversing costs an extra person's willingness to relay
    the intro, and only the traversal side should pay it — builder.add_edge
    stores the un-surcharged tier cost, so provenance stays a property of the
    tie rather than of how someone walked it.

    Every consumer in this module goes through here, which is what keeps the
    reported `warmth_score` ranking the same way Dijkstra does: _adjacency
    compares with it (a constant shifts both sides, so the warmest edge per pair
    is unchanged), _best_path minimizes it, and _describe sums it into
    `total_cost`.
    """
    return taxonomy.edge_cost(edge.relationship_type) + config.HOP_SURCHARGE


def fame_penalty(person: Person) -> float:
    """Routing surcharge for someone famous we do not actually know.

    Reads the STORED qid only — never bridge.is_notable(), which falls back to a
    live Wikipedia lookup. This runs once per person in the graph on every query,
    so a network call here would be thousands of them.

    Someone Drew genuinely knows is reachable regardless of fame, which is why
    is_warm is checked first: Harry Stebbings carries a QID and is also Drew's
    first-degree contact.
    """
    if person.is_warm:
        return 0.0
    if person.wikidata_qid:
        return config.UNREACHABLE_FAME_PENALTY
    return 0.0


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
    #
    # Degree alone does not answer "will this person take the call". Samuel L.
    # Jackson sits at degree 3 and pays nothing here, yet he is the single worst
    # node to route through; Bree Hanson at degree 36 is a real connector. So a
    # second, orthogonal surcharge is added for people who are famous but not
    # actually known to us (see config.UNREACHABLE_FAME_PENALTY).
    thr = config.MEGA_HUB_DEGREE
    node_penalty: Dict[str, float] = {}
    for pid, deg in degree.items():
        penalty = 0.0
        if deg > thr:
            penalty += config.DEGREE_PENALTY_COEF * math.log(deg / thr)
        person = person_by_id.get(pid)
        if person is not None:
            penalty += fame_penalty(person)
        if penalty:
            node_penalty[pid] = penalty
    return adj, person_by_id, src_by_id, node_penalty


def _best_path(adj, start: str, target: str, max_hops: int,
               excluded: Optional[Set[str]] = None,
               node_penalty: Optional[Dict[str, float]] = None,
               banned_steps: Optional[Set[Tuple[str, str]]] = None
               ) -> Optional[List[Hop]]:
    """Lowest-cost (warmest) path, skipping `excluded` intermediate nodes.

    `banned_steps` closes individual (from, to) links rather than whole people,
    which is what lets `_routes` ask for "another way out of Drew" without
    striking Drew's best connector off the map. Directed, and that is enough:
    the reverse of a banned step leads back into a node the caller has already
    excluded.

    Cost of a step = the edge's tier cost + a flat per-hop surcharge + a routing
    penalty for the node being entered (unless it is the target).

    The surcharge is what makes "one introduction beats three" true of the cost
    function and not just of the README. Summing tier costs alone made three
    tier-1 hops (3.0) tie one tier-3 hop (3.0), and made two tier-1 hops (2.0)
    beat it outright — i.e. the search preferred relaying through two strangers
    over asking one person who had actually invested in the target's company.
    Every hop is another human who has to agree to pass the intro along, and
    that risk compounds per hop rather than averaging out; the node penalty does
    not cover it, since it only bites above MEGA_HUB_DEGREE and is zero for the
    ordinary low-degree people such a chain runs through.
    """
    excluded = excluded or set()
    node_penalty = node_penalty or {}
    banned_steps = banned_steps or set()
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
            if (node, neighbor) in banned_steps:
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


def _bridges(path: List[Hop]) -> List[str]:
    """The people who would have to relay the intro — everyone but the ends."""
    return [pid for pid, _edge in path[1:-1]]


def _route_cost(path: List[Hop], node_penalty: Dict[str, float]) -> float:
    """Exactly what _best_path minimized for this path.

    Must stay in step with the loop there: every hop pays its edge cost, and
    every node ENTERED except the target pays its node penalty — which is the
    bridges, since the start is never entered.
    """
    total = sum(_edge_cost(edge) for _pid, edge in path if edge is not None)
    return total + sum(node_penalty.get(pid, 0.0) for pid in _bridges(path))


def _is_a_detour_around(candidate: List[Hop], shown: List[List[Hop]]) -> bool:
    """True when `candidate` is just an accepted route with extra people in it.

    The k cheapest paths in a graph are mostly trivial variations on each other,
    and the variations read as nonsense here. The second-cheapest route to Garry
    Tan was `Drew -> Atlas Berry -> Bryce Johnson -> Garry Tan`: the best route
    with a stranger wedged in front of it. Drew already knows Bryce. Going
    through Atlas to reach him is not a second option, it is the same intro made
    worse, and offering it as a choice is what "three variations on one chain"
    meant.

    Superset of the BRIDGES, which is exactly the "everyone I already had to
    ask, plus more" test. It leaves genuine alternatives alone: a route reaching
    the same warm tail through a different first contact drops someone, so it is
    never a superset. A direct edge has no bridges, so every longer route is a
    detour around it — correct, and the reason a 1-hop answer stands alone.
    """
    bridges = set(_bridges(candidate))
    return any(bridges >= set(_bridges(route)) for route in shown)


def _routes(adj, start: str, target: str, max_hops: int, k: int,
            node_penalty: Optional[Dict[str, float]] = None,
            banned: Optional[Set[str]] = None) -> List[List[Hop]]:
    """The k warmest distinct routes, warmest first — Yen's k-shortest-paths.

    Replaces a homegrown rule that excluded every bridge of every route already
    accepted. That rule is what put celebrities in the results: two good answers
    through Drew's real connectors banned Sophia Amoruso, Turner Novak, Peter
    Rahal AND Harry Stebbings, so the warmest chain still standing to Marc
    Andreessen ran through Joe Rogan. The junk route was not discovered, it was
    manufactured — the search demolished every alternative before asking for
    one, and no amount of re-ranking helps once the good candidates are gone.

    Yen's deviates by closing one LINK at a time rather than deleting people.
    That distinction is the whole fix. Asking "another way out of Drew?" closes
    Drew -> Sophia and leaves Sophia standing, so she is still available deeper
    in the next route — and Harry Stebbings, who every good route to Marc
    Andreessen runs through, is never struck off merely for being useful twice.

    Each round extends every prefix of the last route found: keep the root,
    close the links already taken out of its spur node, bar the root's own nodes
    from being revisited (which is what keeps a route loopless), and re-search
    from there. Candidates pool across rounds and are taken cheapest-first, so
    route 2 is genuinely the second-warmest route and not whichever deviation
    happened to be tried first.

    `explored` and `shown` are deliberately different lists. A detour we would
    never show still has to be deviated FROM, because the route worth showing
    usually sits behind it: the real second-best route to Garry Tan is only
    generated by deviating off `Drew -> Atlas Berry -> Bryce Johnson -> ...`,
    the very candidate _is_a_detour_around discards. Filtering at the pop and
    walking on would stall the search on the first junk candidate and return one
    route where three exist. Yen's bookkeeping reads `explored` for the same
    reason — its correctness depends on knowing every route already generated.

    `banned` nodes may never be TRANSITED (the target is always exempt — see
    _best_path), which is how _plausible_first guarantees a usable lead route.
    """
    node_penalty = node_penalty or {}
    banned = set(banned or ())

    first = _best_path(adj, start, target, max_hops, banned, node_penalty)
    if first is None:
        return []

    explored = [first]               # every route Yen's has generated, in cost order
    shown = [first]                  # the subset a human would call distinct
    seen: Set[Tuple[str, ...]] = {tuple(pid for pid, _e in first)}
    pool: List[Tuple[float, int, List[Hop]]] = []
    counter = itertools.count()      # keeps heapq off the path lists (ORM edges)
    deadline = (time.monotonic() + config.ROUTE_SEARCH_BUDGET_S
                if config.ROUTE_SEARCH_BUDGET_S > 0 else float("inf"))

    while len(shown) < k and len(explored) < config.ROUTE_SEARCH_LIMIT:
        if time.monotonic() > deadline:
            break        # keep what we have; the routes given up on rank worst
        previous = explored[-1]
        for i in range(len(previous) - 1):
            spur_node = previous[i][0]
            root = previous[:i + 1]
            root_sig = tuple(pid for pid, _e in root)

            # Close every link already taken out of this spur node by a route
            # that reached it the same way. Without this the search just returns
            # the route we already have; with it, only that one exit closes.
            banned_steps = {
                (path[i][0], path[i + 1][0]) for path in explored
                if len(path) > i + 1
                and tuple(pid for pid, _e in path[:i + 1]) == root_sig
            }
            # The root's own people are off-limits to the spur, so a route can
            # never loop back through someone it has already used.
            excluded = banned | {pid for pid, _e in root[:-1]}

            spur = _best_path(adj, spur_node, target, max_hops - i, excluded,
                              node_penalty, banned_steps)
            if spur is None:
                continue
            candidate = root + spur[1:]
            signature = tuple(pid for pid, _e in candidate)
            if signature in seen:
                continue
            seen.add(signature)
            heapq.heappush(pool, (_route_cost(candidate, node_penalty),
                                  next(counter), candidate))
        if not pool:
            break            # every distinct route has been found; k was optimistic
        _cost, _tie, next_best = heapq.heappop(pool)
        explored.append(next_best)
        if not _is_a_detour_around(next_best, shown):
            shown.append(next_best)
    return shown


def _serialize(path: List[Hop], person_by_id, src_by_id) -> dict:
    nodes, costs, bridges, unreachable = [], [], [], []
    for i, (pid, edge) in enumerate(path):
        person = person_by_id.get(pid)
        # A famous stranger standing MID-path is the thing that makes a route
        # unusable: the hop is real, but expecting Samuel L. Jackson to pass an
        # intro along to Elon Musk is not a plan. Marked rather than dropped —
        # sometimes it is the only route there is, and the honest answer is to
        # show it and say why it will not work. As the ENDPOINT it is fine: you
        # asked to reach them, and nobody has to relay anything.
        is_bridge = 0 < i < len(path) - 1
        blocked = bool(person is not None and is_bridge and fame_penalty(person))
        node = {
            "label": person.canonical_name if person else pid,
            "is_warm": bool(person.is_warm) if person else False,
            "unreachable": blocked,
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
        if is_bridge:
            bridges.append(node["label"])
            if blocked:
                unreachable.append(node["label"])
        nodes.append(node)

    hops = len(path) - 1
    total = sum(costs)
    return {
        "hops": hops,
        "total_cost": round(total, 2),
        "warmth_score": taxonomy.warmth_score(total, hops),
        "bridges": bridges,
        # Names, not just a flag: "this route needs Samuel L. Jackson to make an
        # introduction" is the sentence that tells you to stop, and it needs the
        # name to land. Empty for the ordinary case.
        "unreachable_bridges": unreachable,
        "usable": not unreachable,
        "path": nodes,
    }


def _lookup(db: Session, name: str) -> Optional[Person]:
    return db.execute(select(Person).where(
        Person.norm_name == person_norm_key(name))).scalar_one_or_none()


def unroutable_bridge_ids(person_by_id: Dict[str, Person]) -> Set[str]:
    """Everyone who may not stand MID-path: famous, and not actually known to us.

    Same test as fame_penalty, used as a hard gate rather than a surcharge. The
    surcharge alone could not do this job: it re-ranks, so it only helps when
    something better exists to re-rank to, and it is a fixed number that a long
    enough chain of warm hops will always out-sum. As a bridge ban the question
    it answers is the right one — not "how much worse is this route" but "is
    this a route at all" — and the answer does not drift with path length.
    """
    return {pid for pid, person in person_by_id.items() if fame_penalty(person)}


def _plausible_first(adj, a: Person, b: Person, person_by_id, node_penalty):
    """Routes for a -> b, led by a walkable one whenever one exists at all.

    Pass 1 bars every famous stranger from the bridge positions, so anything it
    returns is usable by construction. The target is exempt: asking to reach
    Elon Musk by name is a different request from being routed THROUGH him, and
    nobody has to relay anything to the person you named.

    Pass 2 runs only when pass 1 came back empty — i.e. every chain that exists
    needs a celebrity to pass the intro along, which is the honest answer for
    Ira Matthew Ehrenpreis in the bundled graph. It is capped separately and
    hard: showing the one real chain and labelling why it will not work is
    useful, and showing three of them is just noise wearing the same label. The
    cap is why a usable route is never crowded out by variations on a dead end.
    """
    blocked = unroutable_bridge_ids(person_by_id) - {a.id, b.id}
    routes = _routes(adj, a.id, b.id, config.hop_limit(),
                     config.CONNECT_MAX_PATHS, node_penalty, blocked)
    if routes:
        return routes
    return _routes(adj, a.id, b.id, config.hop_limit(),
                   config.CONNECT_MAX_UNUSABLE_PATHS, node_penalty)


def _try_paths(db: Session, a: Person, b: Person, include_weak: bool = False):
    adj, person_by_id, src_by_id, node_penalty = _adjacency(db, include_weak)
    routes = _plausible_first(adj, a, b, person_by_id, node_penalty)
    return routes, person_by_id, src_by_id


def connect_people(db: Session, name_a: str, name_b: str,
                   depth: int = None, progress=None,
                   include_weak: bool = False, hint: str = "") -> dict:
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
                h = hint if name == name_b else ""   # hint is for the target only
                is_target = name == name_b           # so is the homonym guard
                if config.DEEP_SEARCH:
                    enricher.enrich_neighborhood(db, name, depth=2,
                                                 progress=progress, hint=h,
                                                 is_target=is_target)
                else:
                    enricher.enrich_person(db, name, progress=progress, hint=h,
                                           is_target=is_target)
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
                opposite_component=drew_reach, deadline=deadline, hint=hint,
                is_target=True)
            routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak)

    # Stage 3 — context escalation. No structural path exists, but the caller
    # supplied a context for the target and a web-search key is configured. Spend
    # it: web-search the target for co-mentions (steered by the context), then
    # retry ALLOWING those weak links. This is the "use the context + API keys
    # more when the in-graph search fails" path — the links stay tier-6 and
    # labelled, so Rule 0 is untouched; they are just now traversable.
    if not routes and hint and hint.strip() and enricher.comention._available():
        if progress:
            progress(f"[3] no structural path — web-searching {name_b} "
                     f"with your context…")
        enricher.enrich_target_comention(db, name_b, hint=hint, progress=progress)
        routes, person_by_id, src_by_id = _try_paths(db, a, b, include_weak=True)

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
    # Usable first, THEN warmth. A route that needs a celebrity to relay is not
    # a better answer than a colder one you can actually walk, however warm its
    # hops score — and `best` below is what the UI leads with.
    paths.sort(key=lambda p: (not p["usable"], -p["warmth_score"], p["hops"]))
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


def discover(db: Session, name: str, limit: int = 20, depth: int = None,
             hint: str = "", progress=None) -> dict:
    """Warmest reachable people around `name`, by cheapest total path cost."""
    depth = depth or config.CONNECT_DEPTH

    # Only reach for the network when we have nothing on this person yet.
    root = _lookup(db, name)
    if root is None or root.enriched < target_enrichment_level():
        get_enricher().enrich_neighborhood(db, name, depth=depth, hint=hint,
                                           progress=progress, is_target=True)
        root = _lookup(db, name)
    if root is None:
        return {"found": False, "reason": f"'{name}' is not in the graph"}

    adj, person_by_id, src_by_id, node_penalty = _adjacency(db)

    # Dijkstra from the root; keep the cheapest total cost to each person.
    #
    # Costs must match connect(): this used the frozen `edge.cost` column and
    # threw the node penalty away, so it was the one surface that re-tiering,
    # the hop surcharge, and hub avoidance never reached. That is why it filled
    # with celebrities — a podcast edge to Samuel L. Jackson is cheap, and
    # nothing here ever charged for the fact that he will not take the call.
    #
    # Unlike connect(), the penalty is NOT exempted for any node: in a discover
    # listing every person is a suggestion, so being unreachable disqualifies
    # them as a destination exactly as it does as a stepping stone. connect()
    # exempts only its explicit target — asking to reach a celebrity by name is
    # a different request from being handed one unprompted.
    # `hop_cap`, NOT `limit`. These are different quantities and this line used
    # to assign the hop cap over the caller's result count. hop_limit() returns
    # inf by default, so `len(people) >= limit` below was `>= inf` — never true.
    # The cap silently vanished and every caller got the entire reachable set:
    # /discover?limit=20 answered with ~2.4k people, which is why the listing
    # was full of celebrities. They were never ranked in; nothing was ranked out.
    hop_cap = config.hop_limit()
    counter = itertools.count()
    dist: Dict[str, float] = {root.id: 0.0}
    hops_to: Dict[str, int] = {root.id: 0}
    first_edge: Dict[str, RelationshipEdge] = {}
    heap = [(0.0, 0, next(counter), root.id)]
    while heap:
        cost, hops, _t, node = heapq.heappop(heap)
        if cost > dist.get(node, float("inf")) or hops >= hop_cap:
            continue
        for neighbor, edge in adj.get(node, []):
            new_cost = cost + _edge_cost(edge) + node_penalty.get(neighbor, 0.0)
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
