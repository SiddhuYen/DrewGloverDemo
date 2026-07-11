"""Rule 0 (structural only), Rule 1 (mega-hub cap), and the cost function."""
import pytest

from app import config
from app.edges import taxonomy
from app.graph import builder
from app.graph.builder import NonStructuralEdgeError


def _person(db, name):
    return builder.get_or_create_person(db, name)


# --- Rule 0 ----------------------------------------------------------------
def test_cooccurrence_creates_zero_edges(db):
    """Sentence/page co-occurrence must never become an edge."""
    a, b = _person(db, "Drew Glover"), _person(db, "Sam Altman")
    with pytest.raises(NonStructuralEdgeError):
        builder.add_edge(db, a, b, taxonomy.COOCCURRENCE)
    assert db.query(builder.RelationshipEdge).count() == 0


def test_unknown_relationship_type_is_refused(db):
    a, b = _person(db, "Drew Glover"), _person(db, "Jason Calacanis")
    with pytest.raises(NonStructuralEdgeError):
        builder.add_edge(db, a, b, "appeared_on_same_listicle")
    assert db.query(builder.RelationshipEdge).count() == 0


def test_structural_edge_persists_and_is_marked(db):
    a, b = _person(db, "Drew Glover"), _person(db, "Alex Harris")
    edge = builder.add_edge(db, a, b, "cofounder", evidence="Fiat")
    assert edge is not None and edge.structural is True
    assert edge.warmth_tier == 1 and edge.cost == config.WARMTH_TIER_COST[1]


def test_edge_is_undirected_and_deduped(db):
    a, b = _person(db, "Drew Glover"), _person(db, "Alex Harris")
    first = builder.add_edge(db, a, b, "cofounder")
    second = builder.add_edge(db, b, a, "cofounder")  # reversed
    assert first.id == second.id
    assert db.query(builder.RelationshipEdge).count() == 1


def test_self_edge_is_ignored(db):
    a = _person(db, "Drew Glover")
    assert builder.add_edge(db, a, a, "cofounder") is None


# --- Rule 1 ----------------------------------------------------------------
def test_small_firm_materializes_pairwise_edges(db):
    org = builder.get_or_create_org(db, "Fiat Ventures", org_type="firm")
    members = [_person(db, n) for n in ("Drew Glover", "Alex Harris",
                                        "Marcos Fernandez")]
    edges = builder.materialize_org_edges(db, org, members,
                                          relationship_type="cofounder")
    assert len(edges) == 3          # C(3,2)
    assert all(e.structural for e in edges)


def test_mega_hub_materializes_zero_edges(db):
    """A 60-member org yields no pairwise edges — 'both worked at Google' is
    not closeness."""
    org = builder.get_or_create_org(db, "Google LLC", org_type="company")
    members = [_person(db, f"Person{i:03d} Surname") for i in range(60)]
    assert len(members) > config.MAX_ORG_MEMBERS_FOR_EDGES
    assert builder.materialize_org_edges(db, org, members) == []
    assert db.query(builder.RelationshipEdge).count() == 0


def test_exactly_at_the_cap_still_materializes(db):
    org = builder.get_or_create_org(db, "Capped Partners", org_type="firm")
    n = config.MAX_ORG_MEMBERS_FOR_EDGES
    members = [_person(db, f"Alpha{i:03d} Beta") for i in range(n)]
    edges = builder.materialize_org_edges(db, org, members)
    assert len(edges) == n * (n - 1) // 2


def test_recorded_member_count_wins_over_a_partial_roster(db):
    """A source listing 5 of Google's employees cannot sneak past the cap."""
    org = builder.get_or_create_org(db, "Google LLC", org_type="company",
                                    member_count=80000)
    members = [_person(db, f"Only{i} Person") for i in range(5)]
    assert builder.materialize_org_edges(db, org, members) == []


def test_org_member_count_keeps_the_largest_observed(db):
    builder.get_or_create_org(db, "Acme Inc", member_count=8)
    org = builder.get_or_create_org(db, "Acme Inc", member_count=900)
    assert org.member_count == 900
    org = builder.get_or_create_org(db, "Acme Inc", member_count=3)
    assert org.member_count == 900  # never shrinks


# --- cost / warmth ---------------------------------------------------------
def test_warmer_tier_costs_strictly_less():
    costs = [taxonomy.edge_cost(rt) for rt in
             ("cofounder", "board_member", "investor_of",
              "shared_portfolio", "co_speaker")]
    assert costs == sorted(costs)
    assert all(a < b for a, b in zip(costs, costs[1:]))


def test_working_relationships_outrank_a_one_off_interview():
    """The load-bearing re-tier: being partners at the same firm, or
    co-founders, is warmer than a single podcast interview — otherwise every
    long path collapses into 'X interviewed Y interviewed Z'."""
    interview = taxonomy.edge_cost("podcast_guest")
    assert taxonomy.edge_cost("cofounder") < interview
    assert taxonomy.edge_cost("same_firm_partner") < interview
    assert taxonomy.edge_cost("fiat_colleague") < interview


def test_cooccurrence_cost_is_infinite():
    assert taxonomy.edge_cost(taxonomy.COOCCURRENCE) == float("inf")
    assert taxonomy.edge_cost("total_nonsense") == float("inf")


def test_path_cost_is_the_sum_of_edge_costs():
    types = ["podcast_guest", "same_firm_partner", "investor_of"]
    assert taxonomy.path_cost(types) == pytest.approx(1.0 + 2.0 + 3.0)


def test_warmth_score_decreases_with_cost():
    assert taxonomy.warmth_score(1.0, 1) > taxonomy.warmth_score(3.0, 1)
    assert taxonomy.warmth_score(2.0, 2) > taxonomy.warmth_score(14.0, 2)
    assert taxonomy.warmth_score(float("inf"), 2) == 0.0


def test_warmth_score_prefers_a_shorter_chain_of_equal_tier():
    """Regression: averaging cost per hop made 1-, 2- and 5-hop tier-1 chains
    all score 0.5. One introduction beats three, so distance must count."""
    one_hop = taxonomy.warmth_score(taxonomy.path_cost(["podcast_guest"]), 1)
    two_hop = taxonomy.warmth_score(
        taxonomy.path_cost(["podcast_guest", "podcast_guest"]), 2)
    five_hop = taxonomy.warmth_score(
        taxonomy.path_cost(["podcast_guest"] * 5), 5)
    assert one_hop > two_hop > five_hop


def test_every_non_cooccurrence_type_is_structural():
    for rtype, spec in taxonomy.RELATIONSHIPS.items():
        if rtype == taxonomy.COOCCURRENCE:
            assert not spec.structural
        else:
            assert spec.structural and 1 <= spec.tier <= 5


# --- membership (person -> org, never a person-person tie) -----------------
def test_membership_row_has_no_person_b(db):
    person = _person(db, "Tae Hea Nahm")
    org = builder.get_or_create_org(db, "Storm Ventures", org_type="firm")
    edge = builder.add_membership(db, person, org)
    assert edge is not None
    assert edge.person_b_id is None and edge.organization_id == org.id
    assert edge.structural is True


def test_membership_is_never_traversable(db):
    """Membership gives a person their firm so enrichment can fetch its roster.
    It must never let pathfinding hop person -> org -> person for free."""
    from app.graph.connect import _adjacency, _best_path

    a, b = _person(db, "Tae Hea Nahm"), _person(db, "Sanjay Subhedar")
    org = builder.get_or_create_org(db, "Storm Ventures", org_type="firm")
    builder.add_membership(db, a, org)
    builder.add_membership(db, b, org)

    adj, _, _, _ = _adjacency(db)
    assert _best_path(adj, a.id, b.id, config.hop_limit()) is None


def test_membership_is_idempotent(db):
    person = _person(db, "Eric Bahn")
    org = builder.get_or_create_org(db, "Hustle Fund", org_type="firm")
    first = builder.add_membership(db, person, org)
    second = builder.add_membership(db, person, org)
    assert first.id == second.id


def test_membership_records_a_role_on_the_person(db):
    person = _person(db, "Drew Glover")
    org = builder.get_or_create_org(db, "Fiat Ventures", org_type="firm")
    builder.add_membership(db, person, org, role="Co-Founder & GP")
    assert person.meta["roles"]["Fiat Ventures"] == "Co-Founder & GP"


# --- Layer D: portfolios -> co-investment ----------------------------------
def _firm_with_partners(db, firm_name, n):
    """Partners are named from the FULL firm name — using only its first letter
    made every "Firm{k} Ventures" share one roster, so they deduped away."""
    slug = firm_name.replace(" ", "")
    firm = builder.get_or_create_org(db, firm_name, org_type="firm")
    for i in range(n):
        person = _person(db, f"{slug}{i} Partner{i}")
        builder.add_membership(db, person, firm)
    db.flush()
    return firm


def test_company_identity_is_the_domain_not_the_name(db):
    """"Bolt" is a scooter company and a checkout company; "Airship" and
    "AirShip Inc" are one."""
    a = builder.get_or_create_company(db, "Airship", "airship.com")
    b = builder.get_or_create_company(db, "AirShip Inc", "airship.com")
    assert a.id == b.id and a.norm_name == "airship.com"

    scooter = builder.get_or_create_company(db, "Bolt", "bolt.eu")
    checkout = builder.get_or_create_company(db, "Bolt", "bolt.com")
    assert scooter.id != checkout.id


def test_company_without_a_domain_is_not_created(db):
    assert builder.get_or_create_company(db, "Acme", "") is None


def test_a_single_portfolio_page_creates_no_person_edges(db):
    """One firm's page says the FIRM invested. It says nothing about people."""
    firm = _firm_with_partners(db, "Alpha Ventures", 3)
    company = builder.get_or_create_company(db, "Airship", "airship.com")
    assert builder.record_investment(db, company, firm) is True
    db.flush()
    assert builder.materialize_coinvestor_edges(db, company) == []


def test_two_independent_portfolio_pages_share_a_portfolio(db):
    alpha = _firm_with_partners(db, "Alpha Ventures", 3)
    beta = _firm_with_partners(db, "Beta Capital", 3)
    company = builder.get_or_create_company(db, "Airship", "airship.com")
    builder.record_investment(db, company, alpha)
    builder.record_investment(db, company, beta)
    db.flush()

    edges = builder.materialize_coinvestor_edges(db, company)
    assert len(edges) == 15                       # C(6,2)
    assert all(e.relationship_type == "shared_portfolio" for e in edges)
    assert edges[0].warmth_tier == 4              # not tier 3: no deal lead named
    assert "airship.com" in edges[0].evidence_snippet


def test_recording_the_same_investment_twice_is_idempotent(db):
    firm = _firm_with_partners(db, "Alpha Ventures", 2)
    company = builder.get_or_create_company(db, "Airship", "airship.com")
    assert builder.record_investment(db, company, firm) is True
    assert builder.record_investment(db, company, firm) is False


def test_a_widely_backed_company_is_a_hub_and_implies_no_closeness(db):
    """Rule 1 on the INVESTOR set: if enough firms back one company that the
    combined partner list exceeds the cap, it is a hub, not a shared tie."""
    company = builder.get_or_create_company(db, "Unicorn", "unicorn.com")
    for k in range(10):
        firm = _firm_with_partners(db, f"Firm{k} Ventures", 5)   # 50 partners
        builder.record_investment(db, company, firm)
    db.flush()
    assert builder.materialize_coinvestor_edges(db, company) == []


def test_investor_of_remains_unasserted_by_portfolio_pages(db):
    """A portfolio page never names the deal lead or the founders, so Layer D
    must not emit tier-3 `investor_of`. If this ever fires, a source that
    genuinely asserts it must be cited."""
    alpha = _firm_with_partners(db, "Alpha Ventures", 2)
    beta = _firm_with_partners(db, "Beta Capital", 2)
    company = builder.get_or_create_company(db, "Airship", "airship.com")
    builder.record_investment(db, company, alpha)
    builder.record_investment(db, company, beta)
    db.flush()
    edges = builder.materialize_coinvestor_edges(db, company)
    assert {e.relationship_type for e in edges} == {"shared_portfolio"}


# --- tier 3: co-investment from a funding round ----------------------------
def test_a_round_connects_partners_across_firms_only(db):
    """Two partners at the SAME firm are already `same_firm_partner`; a
    co-investor edge between them would be redundant."""
    fiat = _firm_with_partners(db, "Fiat Ventures", 2)
    bonfire = _firm_with_partners(db, "Bonfire Ventures", 3)
    edges = builder.materialize_round_edges(db, [fiat, bonfire],
                                            evidence="Both invested in Odynn.")
    assert len(edges) == 6                       # 2 x 3, never within a firm
    assert all(e.relationship_type == "co_investor" for e in edges)
    assert edges[0].warmth_tier == 2

    firm_of = {}
    for org in (fiat, bonfire):
        for person in builder.people_of_org(db, org):
            firm_of[person.id] = org.id
    assert all(firm_of[e.person_a_id] != firm_of[e.person_b_id] for e in edges)


def test_a_single_investor_is_not_a_co_investment(db):
    fiat = _firm_with_partners(db, "Fiat Ventures", 2)
    assert builder.materialize_round_edges(db, [fiat]) == []


def test_a_round_with_an_unknown_co_investor_creates_no_edges(db):
    """We know nobody at the other firm, so no person-level tie is assertable."""
    fiat = _firm_with_partners(db, "Fiat Ventures", 2)
    unknown = builder.get_or_create_org(db, "Mystery Capital", org_type="firm")
    assert builder.materialize_round_edges(db, [fiat, unknown]) == []


def test_a_mega_round_implies_no_closeness(db):
    firms = [_firm_with_partners(db, f"Fund{k} Ventures", 6) for k in range(8)]
    assert builder.materialize_round_edges(db, firms) == []   # 48 > cap of 40


def test_co_investor_is_warmer_than_shared_portfolio():
    """Same round beats same portfolio: the round is a shared decision."""
    assert taxonomy.edge_cost("co_investor") < taxonomy.edge_cost("shared_portfolio")
    assert taxonomy.warmth_tier("co_investor") == 2


# --- identity --------------------------------------------------------------
def test_homonyms_with_different_qids_stay_separate(db):
    a = builder.get_or_create_person(db, "John Smith", qid="Q111")
    b = builder.get_or_create_person(db, "John Smith", qid="Q222")
    assert a.id != b.id


def test_name_match_adopts_a_qid(db):
    a = builder.get_or_create_person(db, "Marc Benioff")
    b = builder.get_or_create_person(db, "Marc Benioff", qid="Q317162")
    assert a.id == b.id and b.wikidata_qid == "Q317162"
