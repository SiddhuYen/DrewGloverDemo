"""A real edge is not the same thing as a usable introduction.

Rule 0 asks whether a source asserts the tie. It does not ask whether the
person would take the call, and the bundled graph is full of ties that pass the
first test and fail the second: Drew reaches Tesla's board only via
`Andrew Buckley -> Samuel L. Jackson -> Elon Musk`. Every hop there is sourced
and none of it is an intro anyone could ask for.
"""
from app import config
from app.graph import connect


class _Person:
    def __init__(self, name, warm=False, qid=None):
        self.canonical_name = name
        self.is_warm = warm
        self.wikidata_qid = qid


class _Edge:
    def __init__(self, rt="cofounder"):
        self.relationship_type = rt
        self.source_id = None
        self.evidence_snippet = ""


def _serialize(names_and_people, rel="cofounder"):
    people = {n: p for n, p in names_and_people}
    path = [(n, None if i == 0 else _Edge(rel))
            for i, (n, _p) in enumerate(names_and_people)]
    return connect._serialize(path, people, {})


def test_famous_stranger_mid_path_makes_the_route_unusable():
    d = _serialize([
        ("drew", _Person("Drew Glover", warm=True)),
        ("slj", _Person("Samuel L. Jackson", qid="Q172678")),
        ("musk", _Person("Elon Musk", qid="Q317521")),
    ])
    assert d["usable"] is False
    # Named, not just flagged — the name is what tells you to stop.
    assert d["unreachable_bridges"] == ["Samuel L. Jackson"]


def test_famous_person_as_the_TARGET_is_still_a_usable_route():
    """You asked to reach them by name; nobody has to relay anything."""
    d = _serialize([
        ("drew", _Person("Drew Glover", warm=True)),
        ("buckley", _Person("Andrew Buckley")),
        ("slj", _Person("Samuel L. Jackson", qid="Q172678")),
    ])
    assert d["usable"] is True
    assert d["unreachable_bridges"] == []


def test_a_famous_bridge_drew_actually_knows_is_fine():
    """Harry Stebbings has a QID and is Drew's first degree. Fame is only
    disqualifying when it comes with being a stranger."""
    d = _serialize([
        ("drew", _Person("Drew Glover", warm=True)),
        ("harry", _Person("Harry Stebbings", warm=True, qid="Q107277449")),
        ("x", _Person("Some Founder")),
    ])
    assert d["usable"] is True


def test_ordinary_route_is_untouched():
    d = _serialize([
        ("drew", _Person("Drew Glover", warm=True)),
        ("bree", _Person("Bree Hanson", warm=True)),
        ("charles", _Person("Charles Hudson")),
    ])
    assert d["usable"] is True
    assert d["unreachable_bridges"] == []
    assert [n["unreachable"] for n in d["path"]] == [False, False, False]


def test_disabling_the_penalty_stops_flagging():
    original = config.UNREACHABLE_FAME_PENALTY
    try:
        config.UNREACHABLE_FAME_PENALTY = 0.0
        d = _serialize([
            ("drew", _Person("Drew Glover", warm=True)),
            ("slj", _Person("Samuel L. Jackson", qid="Q172678")),
            ("musk", _Person("Elon Musk", qid="Q317521")),
        ])
        assert d["usable"] is True
    finally:
        config.UNREACHABLE_FAME_PENALTY = original


def test_fame_penalty_reads_only_the_stored_qid():
    """It runs per person per query; a live lookup here would be thousands of
    network calls. bridge.is_notable() is the one that may hit Wikipedia."""
    assert connect.fame_penalty(_Person("Nobody")) == 0.0
    assert connect.fame_penalty(_Person("Famous", qid="Q1")) > 0.0
    assert connect.fame_penalty(_Person("Known", warm=True, qid="Q1")) == 0.0


def test_discover_honours_the_caller_limit(db, monkeypatch):
    """`limit` used to be overwritten by config.hop_limit() — a HOP cap, and inf
    by default — so `len(people) >= limit` was `>= inf` and never fired. Every
    caller silently got the entire reachable set; /discover?limit=20 answered
    with thousands of people, which is what filled the listing with celebrities.
    """
    from app.graph import builder, connect as c
    from app.graph.enrich import get_enricher

    root = builder.get_or_create_person(db, "Drew Glover")
    for i in range(40):
        other = builder.get_or_create_person(db, f"Contact {i:02}")
        builder.add_edge(db, root, other, "cofounder")
    db.flush()
    monkeypatch.setattr(get_enricher(), "enrich_neighborhood", lambda *a, **k: None)

    result = c.discover(db, "Drew Glover", limit=5)
    assert result["found"] is True
    assert len(result["neighborhood"]) == 5, "caller's limit ignored again"
    assert result["count"] == 5
