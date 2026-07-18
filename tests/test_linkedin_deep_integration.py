"""Deep search must compose with an imported LinkedIn export and the pre-seeded
graph:

  1. an imported tier-1 `linkedin_1st` edge and a pre-seeded structural edge form
     one route in pathfinding (Drew -> imported connection -> target); and
  2. the frontier the deep-search BFS enriches ranks the owner's OWN warm first
     degree (seeded + imported) ahead of equally-ordinary cold strangers, so a
     path through the user's real network is found inside the fanout budget.

No network: pathfinding and the ranking key are exercised directly.
"""
from app import config
from app.graph import builder
from app.graph.bridge import expansion_rank, rank_frontier
from app.graph.connect import _adjacency, _best_path
from app.ingest.linkedin_csv import ingest_csv


def _p(db, name):
    return builder.get_or_create_person(db, name)


# --- 1. imported edge + pre-seeded edge form one route ----------------------
def test_route_runs_through_an_imported_linkedin_connection(db):
    """Drew imports X; the pre-seeded graph already ties X to the target
    structurally. The warm route Drew ->(linkedin_1st) X ->(podcast) target must
    exist without any enrichment."""
    export = (
        "First Name,Last Name,URL,Email Address,Company,Position\n"
        "Xavier,Bridge,https://linkedin.com/in/xb,,Acme,Partner\n"
    )
    ingest_csv(db, export, owner_name="Drew Glover")

    drew = _p(db, "Drew Glover")
    xavier = _p(db, "Xavier Bridge")           # created by the import
    target = _p(db, "Target Person")
    # pre-seeded structural tie from the imported person to the target
    builder.add_edge(db, xavier, target, "podcast_guest")

    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, target.id, config.hop_limit())

    assert [pid for pid, _e in path] == [drew.id, xavier.id, target.id]
    # and the first hop is the warm imported edge, not something colder
    first_edge = path[1][1]
    assert first_edge.relationship_type == "linkedin_1st"
    assert first_edge.warmth_tier == 1


def test_imported_connection_beats_a_cold_direct_edge(db):
    """A 2-hop route through an imported LinkedIn connection (tier 1) is warmer
    than a 1-hop cold structural edge, so pathfinding prefers it."""
    ingest_csv(db,
               "First Name,Last Name,URL,Email Address,Company,Position\n"
               "Xavier,Bridge,https://linkedin.com/in/xb,,Acme,Partner\n",
               owner_name="Drew Glover")
    drew, xavier, target = (_p(db, "Drew Glover"), _p(db, "Xavier Bridge"),
                            _p(db, "Target Person"))
    builder.add_edge(db, xavier, target, "linkedin_1st")   # X also knows target
    builder.add_edge(db, drew, target, "co_speaker")       # cold direct tie

    adj, _, _, _ = _adjacency(db)
    path = _best_path(adj, drew.id, target.id, config.hop_limit())

    assert [pid for pid, _e in path] == [drew.id, xavier.id, target.id]


# --- 2. the deep-search frontier prefers the owner's real network -----------
def test_frontier_ranks_warm_first_degree_ahead_of_cold_strangers(db):
    """An imported/seeded warm contact must sort before an equally non-notable
    cold stranger, so the fanout budget spends on the user's real network."""
    warm = _p(db, "Warm Contact")
    warm.is_warm = True
    cold = _p(db, "Cold Stranger")
    cold.is_warm = False
    db.flush()

    ranked = rank_frontier(db, [(cold, None), (warm, None)])
    assert ranked[0].id == warm.id            # warm first degree expanded first


def test_warm_priority_is_off_when_building_the_famous_backbone(db):
    """In prefer_notable mode (mapping the seed's famous network) the warm boost
    is neutral — it must not override the famous-first direction."""
    warm = _p(db, "Warm Contact")
    warm.is_warm = True
    db.flush()
    key_down = expansion_rank(db, warm, "")
    key_up = expansion_rank(db, warm, "", prefer_notable=True)
    # walking down, warm sorts to the front (warm_key 0); pushing up it does not.
    assert key_down[2] == 0
    assert key_up[2] == 1
