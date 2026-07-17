"""A route is only worth returning if a human could actually walk it.

These pin the two separate failures behind "why is it offering me Samuel L.
Jackson three times". They are worth naming apart, because only one of them is
about celebrities at all:

  * The search MANUFACTURED the celebrity routes. Diversity worked by banning
    every bridge of every route already accepted, so after two good answers it
    had disqualified the whole bench of real connectors and the warmest chain
    still standing ran through a famous stranger. Fixing the ranking would not
    have touched this — by the time the junk route is scored, it is the only
    candidate left.
  * It then showed them NEXT TO routes that worked, because k slots were filled
    unconditionally.
"""
from app import config
from app.graph import builder
from app.graph.connect import (_adjacency, _plausible_first, _routes,
                               _serialize, unroutable_bridge_ids)


def _p(db, name, qid=None, warm=False):
    return builder.get_or_create_person(db, name, qid=qid, is_warm=warm)


def _link(db, a, b, rtype="cofounder"):
    builder.add_edge(db, a, b, rtype)


def _docs(db, drew, target):
    """What connect_people would rank, without the enrichment machinery."""
    adj, people, srcs, pen = _adjacency(db)
    routes = _plausible_first(adj, drew, target, people, pen)
    docs = [_serialize(r, people, srcs) for r in routes]
    docs.sort(key=lambda d: (not d["usable"], -d["warmth_score"], d["hops"]))
    return docs


def _names(doc):
    return [n["label"] for n in doc["path"]]


def test_a_usable_route_leads_when_one_exists(db):
    """Drew -> Celeb -> Target is one hop; the clean route is three. The short
    chain must not win, and must not even be offered: the whole point is that
    Elon Musk will not relay your intro however few hops away he is."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Ira Ehrenpreis")
    celeb = _p(db, "Elon Musk", qid="Q317521")
    x, h = _p(db, "Bree Hanson"), _p(db, "Charles Hudson")

    _link(db, drew, celeb)
    _link(db, celeb, target)
    _link(db, drew, x)
    _link(db, x, h)
    _link(db, h, target)
    db.flush()

    docs = _docs(db, drew, target)
    assert docs[0]["usable"] is True
    assert _names(docs[0]) == ["Drew Glover", "Bree Hanson", "Charles Hudson",
                               "Ira Ehrenpreis"]
    assert all(d["usable"] for d in docs), "a dead end was shown next to a live one"


def test_an_alternate_reuses_a_shared_hub_instead_of_collapsing_onto_a_celebrity(db):
    """THE regression. Two clean routes (Drew->X->Hub->T and Drew->Y->Hub->T)
    share a hub. Excluding every bridge of route 1 bans X *and* the Hub, and the
    only chain left is the celebrity — so the old search reported a famous
    stranger as the second-best intro while Y sat unused. Deviating around one
    bridge at a time keeps the Hub available to route 2."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Marc Andreessen")
    hub = _p(db, "Harry Stebbings")
    x, y = _p(db, "Turner Novak"), _p(db, "Adam Fishman")
    celeb = _p(db, "Joe Rogan", qid="Q2718421")

    for bridge in (x, y):
        _link(db, drew, bridge)
        _link(db, bridge, hub)
    _link(db, hub, target)
    _link(db, drew, celeb)
    _link(db, celeb, target)
    db.flush()

    docs = _docs(db, drew, target)
    assert len(docs) == 2
    assert all(d["usable"] for d in docs)
    # Both real bridges are offered, and the hub is reused rather than banned.
    assert {_names(d)[1] for d in docs} == {"Turner Novak", "Adam Fishman"}
    assert all("Harry Stebbings" in _names(d) for d in docs)
    assert not any("Joe Rogan" in _names(d) for d in docs)


def test_the_only_route_there_is_gets_shown_and_labelled(db):
    """Drew reaches Ira Ehrenpreis only through Elon Musk. Showing nothing would
    hide a real chain; the honest answer is the chain plus why it is not an
    intro you can ask for."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Ira Ehrenpreis")
    celeb = _p(db, "Elon Musk", qid="Q317521")
    _link(db, drew, celeb)
    _link(db, celeb, target)
    db.flush()

    docs = _docs(db, drew, target)
    assert len(docs) == 1
    assert docs[0]["usable"] is False
    assert docs[0]["unreachable_bridges"] == ["Elon Musk"]


def test_a_dead_end_is_never_told_three_ways(db):
    """Every route needs some celebrity, and there are three to choose from.
    Banning one celebrity's bridge just routes through the next one, which is
    how a listing ends up as three variations on 'you cannot do this'."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Ira Ehrenpreis")
    for name, qid in (("Elon Musk", "Q317521"), ("Joe Rogan", "Q2718421"),
                      ("Samuel L. Jackson", "Q172678")):
        celeb = _p(db, name, qid=qid)
        _link(db, drew, celeb)
        _link(db, celeb, target)
    db.flush()

    docs = _docs(db, drew, target)
    assert len(docs) == config.CONNECT_MAX_UNUSABLE_PATHS == 1
    assert docs[0]["usable"] is False


def test_a_famous_target_is_still_reachable_by_name(db):
    """The ban is on RELAYING, not on being famous. You asked for him."""
    drew = _p(db, "Drew Glover", warm=True)
    buckley = _p(db, "Andrew Buckley")
    slj = _p(db, "Samuel L. Jackson", qid="Q172678")
    _link(db, drew, buckley)
    _link(db, buckley, slj, "co_star")
    db.flush()

    docs = _docs(db, drew, slj)
    assert len(docs) == 1 and docs[0]["usable"] is True
    assert _names(docs[0])[-1] == "Samuel L. Jackson"


def test_a_famous_person_drew_actually_knows_may_still_relay(db):
    """Harry Stebbings carries a QID and is Drew's first degree. The ban is on
    famous STRANGERS; someone Drew genuinely knows is reachable regardless."""
    drew = _p(db, "Drew Glover", warm=True)
    harry = _p(db, "Harry Stebbings", qid="Q107277449", warm=True)
    target = _p(db, "Marc Andreessen")
    _link(db, drew, harry)
    _link(db, harry, target)
    db.flush()

    adj, people, _srcs, _pen = _adjacency(db)
    assert harry.id not in unroutable_bridge_ids(people)
    docs = _docs(db, drew, target)
    assert docs[0]["usable"] is True and "Harry Stebbings" in _names(docs[0])


def test_an_alternate_is_not_the_best_route_with_a_stranger_wedged_in_front(db):
    """Yen's second-cheapest route to Garry Tan was the best route plus one
    extra person: Drew -> Atlas Berry -> Bryce Johnson -> Garry Tan, when Drew
    knows Bryce directly. That is not a second option, it is the same intro made
    worse. Meanwhile the real alternative through Bree is CHEAPER than nothing
    and must still surface — which is why a rejected detour still gets deviated
    from rather than ending the search."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Garry Tan")
    bryce, atlas = _p(db, "Bryce Johnson"), _p(db, "Atlas Berry")
    bree = _p(db, "Bree Hanson")

    _link(db, drew, bryce)                       # the good route
    _link(db, bryce, target)
    _link(db, drew, atlas)                       # the detour: Atlas -> Bryce
    _link(db, atlas, bryce)
    _link(db, drew, bree, "co_speaker")          # a real, colder alternative
    _link(db, bree, target, "co_speaker")
    db.flush()

    docs = _docs(db, drew, target)
    routes = [_names(d) for d in docs]
    assert routes[0] == ["Drew Glover", "Bryce Johnson", "Garry Tan"]
    assert ["Drew Glover", "Atlas Berry", "Bryce Johnson",
            "Garry Tan"] not in routes, "offered the best route, but worse"
    assert ["Drew Glover", "Bree Hanson", "Garry Tan"] in routes, \
        "a genuine alternative was lost behind the detour"


def test_a_direct_edge_stands_alone(db):
    """No bridges means every other route is a detour around it. If Drew knows
    them, 'or you could go through Bree' is noise."""
    drew, marcos = _p(db, "Drew Glover", warm=True), _p(db, "Marcos Fernandez")
    bree = _p(db, "Bree Hanson")
    _link(db, drew, marcos)
    _link(db, drew, bree)
    _link(db, bree, marcos)
    db.flush()

    docs = _docs(db, drew, marcos)
    assert len(docs) == 1
    assert _names(docs[0]) == ["Drew Glover", "Marcos Fernandez"]


def test_routes_are_returned_warmest_first(db):
    """Candidates are pooled and taken cheapest-first, so route 2 is the best
    alternate rather than whichever deviation happened to be generated first."""
    drew = _p(db, "Drew Glover", warm=True)
    target = _p(db, "Charles Hudson")
    for name, rtype in (("Bree Hanson", "cofounder"),        # tier 1
                        ("Vikram Lakhwara", "podcast_guest"),  # tier 2
                        ("Cold Contact", "co_speaker")):       # tier 5
        bridge = _p(db, name)
        _link(db, drew, bridge, rtype)
        _link(db, bridge, target, rtype)
    db.flush()

    docs = _docs(db, drew, target)
    assert [_names(d)[1] for d in docs] == ["Bree Hanson", "Vikram Lakhwara",
                                            "Cold Contact"]
    assert [d["total_cost"] for d in docs] == sorted(d["total_cost"] for d in docs)
