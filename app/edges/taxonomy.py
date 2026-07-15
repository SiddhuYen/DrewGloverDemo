"""Relationship taxonomy, warmth tiers, and the pathfinding cost function.

RULE 0 — STRUCTURAL ASSERTION ONLY.
An edge exists only when a source structurally asserts the tie:
  * a roster / team page that lists both people,
  * a funding announcement naming an investor and a company,
  * an SEC filing or OpenCorporates officer record,
  * a Wikidata claim,
  * a podcast guest entry,
  * a row of the owner's LinkedIn CSV.
Sentence co-occurrence NEVER creates an edge. Two names appearing on the same
VC directory page is not a relationship — that is the exact failure mode that
produced the bogus "Drew -> David Roos -> Jason Calacanis -> Sam Altman" path.
`COOCCURRENCE` is defined here only so callers can name it and drop it.

RULE 1 — CAP ORG FAN-OUT (see builder.materialize_org_edges).
Pairwise person-person edges are materialized inside an org only when its
member count is small (config.MAX_ORG_MEMBERS_FOR_EDGES). A 10-partner VC firm
yields real edges; "both went to Stanford" or "both worked at Google" yields
none, because sharing a mega-institution is not closeness.
"""
from __future__ import annotations

from typing import Dict, NamedTuple

from .. import config


class RelationshipSpec(NamedTuple):
    tier: int
    label: str          # human phrasing, used for "why this intro works"
    structural: bool    # False => may never be persisted (Rule 0)


# tier 1 = warmest (a demonstrated, on-the-record relationship)
# tier 5 = weakest structural affiliation still worth traversing
RELATIONSHIPS: Dict[str, RelationshipSpec] = {
    # Warmth = how well two people actually know each other. A relationship they
    # BUILD or WORK inside daily outranks a one-off touch. In particular a single
    # podcast interview is a real but weak tie — it must not outrank being
    # co-founders or partners at the same firm, or every long-distance path
    # collapses into "X interviewed Y interviewed Z".

    # --- tier 1: built or work together closely ---------------------------
    "cofounder":          RelationshipSpec(1, "co-founded a company together", True),
    "fiat_colleague":     RelationshipSpec(1, "colleagues at Fiat", True),
    "same_firm_partner":  RelationshipSpec(1, "partners at the same firm", True),
    "linkedin_1st":       RelationshipSpec(1, "a direct LinkedIn connection", True),
    "instagram_mutual":   RelationshipSpec(1, "follow each other on Instagram", True),
    "x_mutual":           RelationshipSpec(1, "follow each other on X", True),

    # --- tier 2: an ongoing professional tie ------------------------------
    "board_member":       RelationshipSpec(2, "served on the same board", True),
    "co_investor":        RelationshipSpec(2, "invested in the same round", True),
    "coauthor":           RelationshipSpec(2, "published together", True),
    # `podcast_guest` connects a HOST to a GUEST — the host personally
    # interviewed them (never two guests of the same show). A genuine touch, but
    # a single conversation, so it sits below working relationships.
    "podcast_guest":      RelationshipSpec(2, "sat down together on the podcast", True),

    # --- tier 3: directional or periodic ----------------------------------
    "investor_of":        RelationshipSpec(3, "invested in their company", True),
    "cohost":             RelationshipSpec(3, "co-host the same show", True),
    "colleague":          RelationshipSpec(3, "worked at the same organization", True),
    "family_member":      RelationshipSpec(3, "are family", True),
    "bandmate":           RelationshipSpec(3, "played in the same band", True),
    "teammate":           RelationshipSpec(3, "played on the same team", True),
    "co_inventor":        RelationshipSpec(3, "co-invented a patent", True),

    # --- tier 4: shared professional surface ------------------------------
    "shared_portfolio":   RelationshipSpec(4, "back the same portfolio company", True),
    # A shared film/show cast is structural but weak — the Kevin Bacon effect
    # collapses everyone to ~3 hops, and a cameo shares a cast with the leads.
    # Tier 4 keeps it a last resort so a real tie always outranks it.
    "co_star":            RelationshipSpec(4, "appeared in the same film/show", True),

    # --- tier 5: weak but still asserted ----------------------------------
    "co_speaker":         RelationshipSpec(5, "spoke at the same event", True),
    "notable_affiliation": RelationshipSpec(5, "share a documented affiliation", True),

    # --- tier 6: OPT-IN weak co-occurrence (NOT Rule-0 structural) ---------
    # Two people merely NAMED TOGETHER on a page — a co-occurrence, not an
    # asserted tie. structural=False keeps Rule 0 the default: it is never
    # persisted or traversed unless BOTH gates are opened — config.
    # CO_MENTION_ENABLED to create it, and connect(include_weak=True) to route
    # through it. Tier 6 is punishing, so a real tie of any length outranks it,
    # and every such hop is labelled "not a confirmed relationship".
    "co_mention":         RelationshipSpec(
        6, "were co-mentioned in a source (not a confirmed relationship)", False),

    # --- person -> ORG membership (never a person-person tie) --------------
    # Recorded so a person carries their firm, which is what lets enrichment
    # fetch that firm's roster. It has no person_b, so pathfinding never sees
    # it; org membership becomes closeness only via materialize_org_edges.
    "org_membership":     RelationshipSpec(5, "documented member of the org", True),

    # --- never persisted ---------------------------------------------------
    # Present ONLY so callers have a name for what they must discard.
    "cooccurrence":       RelationshipSpec(99, "appeared on the same page", False),
}

COOCCURRENCE = "cooccurrence"

# The OPT-IN weak tier. NOT structural (Rule 0 stays the default): a co_mention
# is created only when config.CO_MENTION_ENABLED, and traversed only when a
# query passes include_weak=True. `add_edge` allows persisting these; `_adjacency`
# excludes them unless the toggle is on.
WEAK_RELATIONSHIPS = {"co_mention"}


def is_weak(relationship_type: str) -> bool:
    return relationship_type in WEAK_RELATIONSHIPS

# Org membership implies this person-person relationship when materialized.
ORG_TYPE_TO_RELATIONSHIP = {
    "firm": "same_firm_partner",
    "company": "colleague",
    "nonprofit": "board_member",
    "event": "co_speaker",
}


def is_structural(relationship_type: str) -> bool:
    """Rule 0 gate. Unknown types are treated as non-structural (fail closed)."""
    spec = RELATIONSHIPS.get(relationship_type)
    return bool(spec and spec.structural)


def warmth_tier(relationship_type: str) -> int:
    spec = RELATIONSHIPS.get(relationship_type)
    return spec.tier if spec else 5


def label_for(relationship_type: str) -> str:
    spec = RELATIONSHIPS.get(relationship_type)
    return spec.label if spec else "share a documented connection"


def edge_cost(relationship_type: str) -> float:
    """Pathfinding weight. Lower = warmer. A non-traversable type costs
    infinity, so even a buggy caller can never route through one. The weak
    co-occurrence tier IS traversable (at a punishing tier-6 cost) — but only
    reaches `_adjacency` when a query opts into it."""
    if is_structural(relationship_type) or is_weak(relationship_type):
        return config.WARMTH_TIER_COST[warmth_tier(relationship_type)]
    return float("inf")


def path_cost(relationship_types) -> float:
    """Total cost of a path = sum of its edge costs."""
    return sum(edge_cost(rt) for rt in relationship_types)


def warmth_score(total_cost: float, hops: int = 0) -> float:
    """Normalized inverse of TOTAL path cost, in (0, 1]. Lower cost => warmer.

    Deliberately a function of total cost, not of average per-hop cost. Dividing
    by hops measures only the quality of each relationship and throws away
    distance: a 1-hop co-founder link, a 2-hop chain, and a 5-hop chain of
    tier-1 edges would all score exactly 0.5. Distance is the whole point of a
    warm intro — one introduction beats three — and total cost already encodes
    both length and tier, so ranking by it agrees with what Dijkstra minimized.

        1 hop, tier 1  -> 0.5     2 hops, tier 1 -> 0.333
        1 hop, tier 3  -> 0.25    5 hops, tier 1 -> 0.167
    """
    if hops <= 0 and total_cost <= 0:
        return 1.0
    if total_cost == float("inf"):
        return 0.0
    return round(1.0 / (1.0 + total_cost), 3)
