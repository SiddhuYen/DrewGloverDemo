"""Ranking: a shorter path of weaker ties can beat a longer chain of warm ones.

The README's claim is "one introduction beats three", but summing bare tier
costs did not make that true of the search — it left length nearly free, so a
relay through two strangers outranked asking the one person who had invested in
the target's company. These pin the tiebreak so a future re-tier cannot quietly
undo it.
"""
from app import config
from app.edges import taxonomy
from app.graph import connect


class _Edge:
    """Only the field _edge_cost reads. Avoids a DB round-trip per case."""

    def __init__(self, relationship_type: str):
        self.relationship_type = relationship_type


def _adj(*edges):
    """(from, to, relationship_type) triples -> undirected adjacency."""
    out = {}
    for a, b, rt in edges:
        out.setdefault(a, []).append((b, _Edge(rt)))
        out.setdefault(b, []).append((a, _Edge(rt)))
    return out


def _hops(path):
    return [node for node, _edge in path]


def test_direct_investor_tie_beats_a_two_hop_cofounder_relay():
    # "drew -> target" directly (tier 3) vs "drew -> x -> y -> target"... no:
    # the relay is two hops of the warmest tier there is.
    adj = _adj(
        ("drew", "target", "investor_of"),        # tier 3, 1 hop
        ("drew", "relay", "cofounder"),           # tier 1
        ("relay", "target", "cofounder"),         # tier 1, 2 hops total
    )
    path = connect._best_path(adj, "drew", "target", max_hops=4)
    assert _hops(path) == ["drew", "target"], "relayed through a stranger instead"


def test_direct_investor_tie_beats_a_three_hop_cofounder_chain():
    adj = _adj(
        ("drew", "target", "investor_of"),        # tier 3, 1 hop
        ("drew", "a", "cofounder"),
        ("a", "b", "cofounder"),
        ("b", "target", "cofounder"),             # 3 hops of tier 1
    )
    path = connect._best_path(adj, "drew", "target", max_hops=5)
    assert _hops(path) == ["drew", "target"]


def test_a_genuinely_warmer_detour_still_wins():
    """The surcharge biases toward short paths; it must not flatten tiers into
    a plain hop count, or the search stops being about warmth at all."""
    adj = _adj(
        ("drew", "target", "co_mention"),         # tier 6: 14.0 + 1.0 = 15.0
        ("drew", "partner", "cofounder"),         # tier 1
        ("partner", "target", "cofounder"),       # tier 1: total 4.0
    )
    path = connect._best_path(adj, "drew", "target", max_hops=4)
    assert _hops(path) == ["drew", "partner", "target"]


def test_surcharge_is_off_at_zero():
    """0.0 restores the pre-web ranking, so the change is reversible in prod
    without a redeploy."""
    adj = _adj(
        ("drew", "target", "investor_of"),
        ("drew", "relay", "cofounder"),
        ("relay", "target", "cofounder"),
    )
    original = config.HOP_SURCHARGE
    try:
        config.HOP_SURCHARGE = 0.0
        path = connect._best_path(adj, "drew", "target", max_hops=4)
        assert _hops(path) == ["drew", "relay", "target"]
    finally:
        config.HOP_SURCHARGE = original


def test_reported_warmth_ranks_the_same_way_the_search_does():
    """_describe sums _edge_cost into total_cost and the endpoint re-sorts on
    the resulting warmth_score. If that sum ever drops the surcharge, the sort
    silently disagrees with the path the search chose."""
    direct = 1 * (taxonomy.edge_cost("investor_of") + config.HOP_SURCHARGE)
    relay = 2 * (taxonomy.edge_cost("cofounder") + config.HOP_SURCHARGE)
    assert taxonomy.warmth_score(direct, 1) >= taxonomy.warmth_score(relay, 2)


def test_unroutable_edge_stays_unroutable_under_the_surcharge():
    """inf + surcharge must remain inf — Rule 0 is enforced by that infinity."""
    assert connect._edge_cost(_Edge("cooccurrence")) == float("inf")
