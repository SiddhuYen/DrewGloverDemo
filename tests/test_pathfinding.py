"""Pathfinding: warmest route wins, routes are diverse, no path is fabricated."""
from app import config
from app.graph import builder
from app.graph.connect import _adjacency, _best_path, _diverse_paths


def _p(db, name):
    return builder.get_or_create_person(db, name)


def _chain(db, names, rtype="podcast_guest"):
    people = [_p(db, n) for n in names]
    for a, b in zip(people, people[1:]):
        builder.add_edge(db, a, b, rtype)
    return people


def test_finds_the_two_hop_path(db):
    drew, bree, charles = _chain(db, ["Drew Glover", "Bree Hanson", "Charles Hudson"])
    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, charles.id, config.hop_limit())
    assert [pid for pid, _e in path] == [drew.id, bree.id, charles.id]


def test_prefers_the_warmer_route_over_the_shorter_one(db):
    """A 2-hop tier-1 chain (cost 2.0) beats a 1-hop tier-5 edge (cost 7.0)."""
    drew, bree, target = [_p(db, n) for n in
                          ("Drew Glover", "Bree Hanson", "Target Person")]
    builder.add_edge(db, drew, bree, "podcast_guest")
    builder.add_edge(db, bree, target, "podcast_guest")
    builder.add_edge(db, drew, target, "co_speaker")   # direct but cold

    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, target.id, config.hop_limit())
    assert [pid for pid, _e in path] == [drew.id, bree.id, target.id]


def test_diverse_paths_avoid_earlier_bridges(db):
    drew, target = _p(db, "Drew Glover"), _p(db, "Charles Hudson")
    bree, vikram = _p(db, "Bree Hanson"), _p(db, "Vikram Lakhwara")
    for bridge in (bree, vikram):
        builder.add_edge(db, drew, bridge, "podcast_guest")
        builder.add_edge(db, bridge, target, "podcast_guest")

    adj, _, _, _ = _adjacency(db)
    routes = _diverse_paths(adj, drew.id, target.id, config.hop_limit(), 3)
    assert len(routes) == 2                       # only two distinct bridges exist
    bridges = {r[1][0] for r in routes}
    assert bridges == {bree.id, vikram.id}        # genuinely different routes


def test_a_direct_edge_yields_exactly_one_route(db):
    """Regression: a 1-hop path has no bridge to exclude, so re-running Dijkstra
    returned the identical route k times (three copies of 'Drew -> Marcos')."""
    drew, marcos = _p(db, "Drew Glover"), _p(db, "Marcos Fernandez")
    builder.add_edge(db, drew, marcos, "cofounder")

    adj, _, _, _ = _adjacency(db)
    routes = _diverse_paths(adj, drew.id, marcos.id, config.hop_limit(),
                            config.CONNECT_MAX_PATHS)
    assert len(routes) == 1


def test_diverse_paths_never_repeats_a_route(db):
    drew, bree, target = [_p(db, n) for n in
                          ("Drew Glover", "Bree Hanson", "Only Bridge")]
    builder.add_edge(db, drew, bree, "podcast_guest")
    builder.add_edge(db, bree, target, "podcast_guest")

    adj, _, _, _ = _adjacency(db)
    routes = _diverse_paths(adj, drew.id, target.id, config.hop_limit(), 3)
    signatures = [tuple(pid for pid, _e in r) for r in routes]
    assert len(signatures) == len(set(signatures)) == 1


def test_hop_distance_separates_distance_from_unreachability(db):
    """"6 hops apart" and "no chain exists" are different facts. Still used to
    explain a refusal when someone configures an explicit hop bound."""
    from app.graph.connect import _hop_distance

    chain = _chain(db, [f"P{i} Surname" for i in range(7)])   # 6 hops end-to-end
    stranger = _p(db, "Unreachable Person")
    adj, _, _, _ = _adjacency(db)

    assert _hop_distance(adj, chain[0].id, chain[-1].id) == 6
    assert _hop_distance(adj, chain[0].id, stranger.id) is None
    assert _hop_distance(adj, chain[0].id, chain[0].id) == 0


def test_no_path_when_components_are_disconnected(db):
    drew = _p(db, "Drew Glover")
    stranger = _p(db, "Unreachable Person")
    builder.add_edge(db, drew, _p(db, "Bree Hanson"), "podcast_guest")

    adj, _, _, _ = _adjacency(db)
    assert _best_path(adj, drew.id, stranger.id, config.hop_limit()) is None


def test_a_long_path_is_returned_when_the_hop_limit_is_lifted(db):
    """Hops are no longer capped by default. A six-hop chain of asserted
    relationships is a real chain — Drew reaches Alexis Ohanian in six — and
    warmth already penalises the distance, so refusing it hid true answers."""
    names = ["P0 Zero", "P1 One", "P2 Two", "P3 Three", "P4 Four",
             "P5 Five", "P6 Six"]
    people = _chain(db, names)                    # 6 hops end-to-end
    adj, _, _, _ = _adjacency(db)

    path = _best_path(adj, people[0].id, people[-1].id, config.hop_limit())
    assert path is not None and len(path) == 7

    # An explicit bound still bounds.
    assert _best_path(adj, people[0].id, people[-1].id, 5) is None
    assert _best_path(adj, people[0].id, people[-1].id, 6) is not None


def test_hop_limit_is_unlimited_by_default():
    assert config.MAX_HOPS == 0
    assert config.hop_limit() == float("inf")
    assert config.hop_limit(4) == 4.0


def test_adjacency_keeps_the_warmest_edge_per_pair(db):
    a, b = _p(db, "Drew Glover"), _p(db, "Alex Harris")
    src1 = builder.get_or_create_source(db, "https://example.com/one")
    src2 = builder.get_or_create_source(db, "https://example.com/two")
    builder.add_edge(db, a, b, "co_speaker", source=src1)      # tier 5
    builder.add_edge(db, a, b, "cofounder", source=src2)       # tier 1

    adj, _, _, _ = _adjacency(db)
    edge = adj[a.id][0][1]
    assert edge.relationship_type == "cofounder" and edge.cost == 1.0
    assert len(adj[a.id]) == 1


def test_adjacency_excludes_a_non_structural_edge_forced_into_the_db(db):
    """Defense in depth: even a hand-edited row cannot be routed through."""
    from app.models import RelationshipEdge

    a, b = _p(db, "Drew Glover"), _p(db, "Sam Altman")
    db.add(RelationshipEdge(person_a_id=a.id, person_b_id=b.id,
                            relationship_type="cooccurrence",
                            warmth_tier=5, cost=1.0, structural=False))
    db.flush()
    adj, _, _, _ = _adjacency(db)
    assert _best_path(adj, a.id, b.id, config.hop_limit()) is None


def test_best_path_is_stable_when_costs_tie(db):
    """Equal-cost parallel routes must not crash heapq by comparing ORM edges."""
    drew, target = _p(db, "Drew Glover"), _p(db, "Target Person")
    for i in range(5):
        mid = _p(db, f"Bridge{i} Person")
        builder.add_edge(db, drew, mid, "podcast_guest")
        builder.add_edge(db, mid, target, "podcast_guest")

    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, target.id, config.hop_limit())
    assert path is not None and len(path) == 3
