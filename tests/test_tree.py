"""Network tree + comparison semantics."""
import pytest

from app import config
from app.graph import builder
from app.graph.connect import _adjacency
from app.graph.tree import _chain_avoids, _dijkstra


def _p(db, name):
    return builder.get_or_create_person(db, name)


def _link(db, a, b, rtype="podcast_guest"):
    builder.add_edge(db, a, b, rtype)


def test_dijkstra_parent_is_the_warmest_introducer(db):
    drew, bree, target = [_p(db, n) for n in
                          ("Drew Glover", "Bree Hanson", "Charles Hudson")]
    cold = _p(db, "Cold Bridge")
    _link(db, drew, bree, "podcast_guest")       # tier 1
    _link(db, bree, target, "podcast_guest")     # tier 1
    _link(db, drew, cold, "co_speaker")          # tier 5
    _link(db, cold, target, "co_speaker")        # tier 5

    adj, _, _, _ = _adjacency(db)
    _cost, _hops, parent = _dijkstra(adj, drew.id, config.hop_limit())
    assert parent[target.id][0] == bree.id       # warmest, not shortest-by-luck


def test_dijkstra_never_routes_through_a_banned_node(db):
    """A contact Sheel can reach only via Drew belongs to Drew, not to both."""
    drew, sheel, marcos = [_p(db, n) for n in
                           ("Drew Glover", "Sheel Mohnot", "Marcos Fernandez")]
    _link(db, sheel, drew, "podcast_guest")
    _link(db, drew, marcos, "cofounder")

    adj, _, _, _ = _adjacency(db)
    cost, _hops, parent = _dijkstra(adj, sheel.id, config.hop_limit(),
                                    banned={drew.id})
    assert drew.id in cost                  # a banned node may be REACHED
    assert marcos.id not in cost            # but never TRAVERSED


def test_chain_avoids_detects_a_route_through_a_banned_node(db):
    a, mid, far = [_p(db, n) for n in ("A Person", "Mid Person", "Far Person")]
    _link(db, a, mid)
    _link(db, mid, far)
    adj, _, _, _ = _adjacency(db)
    _c, _h, parent = _dijkstra(adj, a.id, config.hop_limit())
    assert _chain_avoids(far.id, parent, banned=set())
    assert not _chain_avoids(far.id, parent, banned={mid.id})


def test_build_tree_reports_hops_tiers_and_hubs(db, monkeypatch):
    from app.graph import tree as tree_mod

    drew, bree = _p(db, "Drew Glover"), _p(db, "Bree Hanson")
    guests = [_p(db, f"Guest{i} Surname") for i in range(4)]
    _link(db, drew, bree, "podcast_guest")
    for guest in guests:
        _link(db, bree, guest, "podcast_guest")

    monkeypatch.setattr(tree_mod, "_ensure", lambda db_, name, depth, progress=None: drew)
    result = tree_mod.build_tree(db, "Drew Glover", max_hops=3)

    assert result["found"] and result["reachable"] == 5
    assert result["by_hop"] == {1: 1, 2: 4}
    assert result["by_tier"] == {2: 5}          # podcast_guest is tier 2
    assert result["hubs"][0]["label"] == "Bree Hanson"
    assert result["hubs"][0]["introduces"] == 4
    assert result["tree"]["label"] == "Drew Glover"
    assert result["tree"]["children"][0]["label"] == "Bree Hanson"


def test_compare_excludes_contacts_reachable_only_through_the_other(db, monkeypatch):
    """Regression: Marcos Fernandez was listed as a mutual contact of Drew and
    Sheel, via `Sheel -> Bree -> Drew Glover -> Marcos`."""
    from app.graph import tree as tree_mod

    drew, sheel, bree = [_p(db, n) for n in
                         ("Drew Glover", "Sheel Mohnot", "Bree Hanson")]
    marcos = _p(db, "Marcos Fernandez")
    _link(db, drew, bree, "podcast_guest")
    _link(db, sheel, bree, "podcast_guest")
    _link(db, drew, marcos, "cofounder")

    lookup = {"Drew Glover": drew, "Sheel Mohnot": sheel}
    monkeypatch.setattr(tree_mod, "_ensure",
                        lambda db_, name, depth, progress=None: lookup[name])
    result = tree_mod.compare_trees(db, "Drew Glover", "Sheel Mohnot", radius=3)

    names = [m["label"] for m in result["mutual_contacts"]]
    assert names == ["Bree Hanson"]
    assert "Marcos Fernandez" not in names
    assert result["only_a"] == 1          # Marcos is Drew's alone
    assert result["directly_connected"] and result["hops_between"] == 2


def test_mutual_contact_chains_carry_evidence_and_source(db, monkeypatch):
    """A bare list of names is unusable for actually asking for the intro:
    each hop must say why it exists and where that came from."""
    from app.graph import tree as tree_mod

    drew, sheel, bree = [_p(db, n) for n in
                         ("Drew Glover", "Sheel Mohnot", "Bree Hanson")]
    src = builder.get_or_create_source(db, "https://drinkswithavc.example/ep37")
    builder.add_edge(db, drew, bree, "podcast_guest", source=src,
                     evidence="Bree Hanson interviewed Drew Glover on DWAVC.")
    builder.add_edge(db, sheel, bree, "podcast_guest", source=src,
                     evidence="Bree Hanson interviewed Sheel Mohnot on DWAVC.")

    lookup = {"Drew Glover": drew, "Sheel Mohnot": sheel}
    monkeypatch.setattr(tree_mod, "_ensure",
                        lambda db_, name, depth, progress=None: lookup[name])
    result = tree_mod.compare_trees(db, "Drew Glover", "Sheel Mohnot", radius=2)

    hop = result["mutual_contacts"][0]["chain_from_a"][0]
    assert hop["label"] == "Bree Hanson"
    assert hop["from"] == "Drew Glover"
    assert hop["relationship"] == "podcast_guest"
    assert hop["warmth_tier"] == 2
    assert hop["why"] == "sat down together on the podcast"
    assert "interviewed Drew Glover" in hop["evidence_snippet"]
    assert hop["source_url"] == "https://drinkswithavc.example/ep37"


def test_compare_radius_bounds_the_network(db, monkeypatch):
    """Full reachability inside one component is always 100% overlap, which
    tells you nothing. The radius is what makes the comparison mean something."""
    from app.graph import tree as tree_mod

    drew, sheel, bree = [_p(db, n) for n in
                         ("Drew Glover", "Sheel Mohnot", "Bree Hanson")]
    far = _p(db, "Far Contact")
    _link(db, drew, bree)
    _link(db, sheel, bree)
    _link(db, bree, far)

    lookup = {"Drew Glover": drew, "Sheel Mohnot": sheel}
    monkeypatch.setattr(tree_mod, "_ensure",
                        lambda db_, name, depth, progress=None: lookup[name])

    tight = tree_mod.compare_trees(db, "Drew Glover", "Sheel Mohnot", radius=1)
    assert [m["label"] for m in tight["mutual_contacts"]] == ["Bree Hanson"]

    wide = tree_mod.compare_trees(db, "Drew Glover", "Sheel Mohnot", radius=2)
    assert {m["label"] for m in wide["mutual_contacts"]} == {"Bree Hanson",
                                                             "Far Contact"}


def test_compare_reports_no_overlap_honestly(db, monkeypatch):
    from app.graph import tree as tree_mod

    drew, bree = _p(db, "Drew Glover"), _p(db, "Bree Hanson")
    immad, harry = _p(db, "Immad Akhund"), _p(db, "Harry Stebbings")
    _link(db, drew, bree)
    _link(db, immad, harry)

    lookup = {"Drew Glover": drew, "Immad Akhund": immad}
    monkeypatch.setattr(tree_mod, "_ensure",
                        lambda db_, name, depth, progress=None: lookup[name])
    result = tree_mod.compare_trees(db, "Drew Glover", "Immad Akhund", radius=2)

    assert result["shared"] == 0
    assert result["overlap_pct"] == 0.0
    assert result["mutual_contacts"] == []
    assert result["directly_connected"] is False


def test_fame_gradient_inverts_with_prefer_notable(db, monkeypatch):
    """Default expansion walks DOWN fame (ordinary first); the push-up backbone
    build walks UP (famous first). Same ranker, one flag."""
    from app.graph import bridge

    famous = _p(db, "Famous Person")
    ordinary = _p(db, "Ordinary Person")
    # 'famous' has a QID (notable); 'ordinary' does not.
    monkeypatch.setattr(bridge, "is_notable",
                        lambda db_, p: p.id == famous.id)
    monkeypatch.setattr(bridge, "_degree", lambda db_, p: 0)

    down = bridge.rank_frontier(db, [ordinary, famous], prefer_notable=False)
    assert down[0].id == ordinary.id          # ordinary first (walk down)

    up = bridge.rank_frontier(db, [ordinary, famous], prefer_notable=True)
    assert up[0].id == famous.id              # famous first (walk up)


def test_compare_refuses_the_same_person(db, monkeypatch):
    from app.graph import tree as tree_mod
    drew = _p(db, "Drew Glover")
    monkeypatch.setattr(tree_mod, "_ensure",
                        lambda db_, name, depth, progress=None: drew)
    result = tree_mod.compare_trees(db, "Drew Glover", "Drew Glover")
    assert not result["found"] and "same person" in result["reason"]
