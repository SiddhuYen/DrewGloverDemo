"""Graph persistence: dedup-aware upserts, and the two edge-quality rules.

`add_edge` is the ONLY function that writes a RelationshipEdge, so Rule 0 is
enforced at the single chokepoint. `materialize_org_edges` is the only function
that turns org membership into person-person edges, so Rule 1 likewise.

Dedup keys:
  people  -> person_norm_key, with wikidata_qid as an authoritative override
  orgs    -> org_norm_key (trailing legal suffix stripped)
  sources -> url
  edges   -> (sorted person pair, relationship_type, source_url)
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..edges import taxonomy
from ..edges.names import (
    detect_org_type,
    looks_like_person_name,
    name_variants,
    org_norm_key,
    person_norm_key,
    strip_role_affixes,
)
from ..models import Organization, Person, RelationshipEdge, Source


class NonStructuralEdgeError(ValueError):
    """Raised when a caller tries to persist an edge that no source asserts.

    Rule 0 is a correctness invariant, not a preference: a co-occurrence edge
    silently poisons every path that runs through it. Fail loudly instead.
    """


# --- entity upserts --------------------------------------------------------
def get_or_create_person(db: Session, name: str, qid: Optional[str] = None,
                         is_warm: bool = False,
                         allow_create: bool = True) -> Optional[Person]:
    """Resolve a person node, disambiguating homonyms by Wikidata QID.

    Identity rules:
      - qid given: a same-QID node wins (authoritative merge across name
        variants). A name-match carrying NO qid adopts this one. A name-match
        with a DIFFERENT qid is a genuine homonym and gets its own node, so two
        unrelated people who share a name never collapse into a false bridge.
      - no qid: plain normalized-name dedup.
    """
    norm = person_norm_key(name)
    if not norm:
        return None

    person = None
    if qid:
        person = db.execute(
            select(Person).where(Person.wikidata_qid == qid)
        ).scalar_one_or_none()
        if person is None:
            by_name = db.execute(
                select(Person).where(Person.norm_name == norm)
            ).scalar_one_or_none()
            if by_name is not None and not by_name.wikidata_qid:
                by_name.wikidata_qid = qid          # same person; adopt the QID
                person = by_name
            elif by_name is not None:
                # name collision with a different QID -> a distinct human
                if not allow_create:
                    return None
                person = _new_person(db, name, f"{norm}#{qid}", qid)
    else:
        person = db.execute(
            select(Person).where(Person.norm_name == norm)
        ).scalar_one_or_none()

    if person is None:
        if not allow_create:
            return None
        person = _new_person(db, name, norm, qid)
    else:
        _merge_aliases(person, name)

    if is_warm and not person.is_warm:
        person.is_warm = True
    return person


def _new_person(db: Session, name: str, norm: str, qid: Optional[str]) -> Person:
    person = Person(
        canonical_name=name.strip(),
        norm_name=norm,
        wikidata_qid=qid,
        aliases=sorted(v for v in name_variants(name) if v != name.strip()),
        meta={},
    )
    db.add(person)
    db.flush()
    return person


def _merge_aliases(person: Person, surface: str) -> None:
    aliases = set(person.aliases or [])
    for v in name_variants(surface):
        if v and v != person.canonical_name:
            aliases.add(v)
    # prefer the longest surface form as the display name
    if len(surface.strip()) > len(person.canonical_name):
        aliases.add(person.canonical_name)
        person.canonical_name = surface.strip()
        aliases.discard(person.canonical_name)
    if aliases != set(person.aliases or []):
        person.aliases = sorted(aliases)


def get_or_create_org(db: Session, name: str, org_type: str = "",
                      member_count: int = 0,
                      allow_create: bool = True) -> Optional[Organization]:
    norm = org_norm_key(name)
    if not norm:
        return None
    org_type = org_type or detect_org_type(name)
    existing = db.execute(
        select(Organization).where(Organization.norm_name == norm)
    ).scalar_one_or_none()
    if existing:
        if existing.type == "unknown" and org_type != "unknown":
            existing.type = org_type
        # Keep the largest observed roster: Rule 1 must fail safe. If one source
        # shows 8 members and another 900, the org is a mega-hub.
        if member_count > (existing.member_count or 0):
            existing.member_count = member_count
        return existing
    if not allow_create:
        return None
    org = Organization(name=name.strip(), norm_name=norm, type=org_type,
                       member_count=member_count, meta={})
    db.add(org)
    db.flush()
    return org


def get_or_create_company(db: Session, name: str, domain: str) -> Optional[Organization]:
    """A portfolio company, identified by its DOMAIN rather than its name.

    Two firms spell the same company differently, and different companies share
    a name — "Bolt" is a scooter company and a checkout company. The domain a
    portfolio page links out to is the identity, so it becomes the dedup key.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return None
    existing = db.execute(
        select(Organization).where(Organization.norm_name == domain)
    ).scalar_one_or_none()
    if existing:
        return existing
    company = Organization(name=(name or domain).strip(), norm_name=domain,
                           type="company", meta={"domain": domain})
    db.add(company)
    db.flush()
    return company


def record_investment(db: Session, company: Organization, firm: Organization,
                      source: Optional[Source] = None) -> bool:
    """Record that `firm`'s portfolio page lists `company`. Returns True if new.

    This is an org-level fact and creates NO person-person edge on its own. A
    portfolio page says the FIRM invested; it never says which partner did, and
    it does not name the founders. Closeness between people is derived later,
    under the Rule 1 cap, and only from two INDEPENDENT such assertions.
    """
    if company is None or firm is None:
        return False
    meta = dict(company.meta or {})
    investors = dict(meta.get("investors") or {})
    key = firm.norm_name
    if key in investors:
        return False
    investors[key] = {"firm": firm.name,
                      "source_url": source.url if source else ""}
    meta["investors"] = investors
    company.meta = meta
    return True


def get_or_create_source(db: Session, url: str, title: str = "",
                         provider: str = "", query_used: str = "") -> Optional[Source]:
    if not url:
        return None
    existing = db.execute(select(Source).where(Source.url == url)).scalar_one_or_none()
    if existing:
        return existing
    source = Source(url=url, title=title, provider=provider, query_used=query_used)
    db.add(source)
    db.flush()
    return source


# --- Rule 0: the only edge writer -----------------------------------------
def add_edge(db: Session, person_a: Person, person_b: Person,
             relationship_type: str, *, source: Optional[Source] = None,
             evidence: str = "", organization: Optional[Organization] = None
             ) -> Optional[RelationshipEdge]:
    """Persist one undirected person-person edge, enforcing RULE 0.

    An edge is written only when `relationship_type` names a structurally
    asserted tie. A co-occurrence type raises rather than silently no-ops,
    because a caller that reaches for one has a bug worth surfacing.

    The pair is stored sorted, so (a,b) and (b,a) are the same row. On a
    duplicate (pair, type, source) we keep the WARMEST tier seen.
    """
    if person_a is None or person_b is None or person_a.id == person_b.id:
        return None
    if not (taxonomy.is_structural(relationship_type)
            or taxonomy.is_weak(relationship_type)):
        raise NonStructuralEdgeError(
            f"refusing to persist a non-structural edge: {relationship_type!r} "
            f"({person_a.canonical_name} <-> {person_b.canonical_name}). "
            "Only a source that structurally asserts the tie may create an edge."
        )

    a_id, b_id = sorted((person_a.id, person_b.id))
    source_id = source.id if source else None
    tier = taxonomy.warmth_tier(relationship_type)
    cost = taxonomy.edge_cost(relationship_type)

    existing = db.execute(
        select(RelationshipEdge).where(
            RelationshipEdge.person_a_id == a_id,
            RelationshipEdge.person_b_id == b_id,
            RelationshipEdge.relationship_type == relationship_type,
            RelationshipEdge.source_id == source_id,
        )
    ).scalar_one_or_none()
    if existing:
        if tier < existing.warmth_tier:
            existing.warmth_tier, existing.cost = tier, cost
        return existing

    edge = RelationshipEdge(
        person_a_id=a_id, person_b_id=b_id,
        organization_id=organization.id if organization else None,
        relationship_type=relationship_type,
        warmth_tier=tier, cost=cost,
        evidence_snippet=evidence or None,
        source_id=source_id,
        structural=True,
    )
    db.add(edge)
    db.flush()
    return edge


def add_membership(db: Session, person: Person, org: Organization, *,
                   source: Optional[Source] = None, evidence: str = "",
                   role: str = "") -> Optional[RelationshipEdge]:
    """Record that `person` belongs to `org` (a person->org row, person_b NULL).

    This is NOT a relationship between people and is never traversed: pathfinding
    only reads rows with a person_b. Its purpose is to give a person their firm,
    so enrichment can go fetch that firm's roster — the step that turns a lone
    podcast guest into a partner with colleagues.
    """
    if person is None or org is None:
        return None
    existing = db.execute(
        select(RelationshipEdge).where(
            RelationshipEdge.person_a_id == person.id,
            RelationshipEdge.person_b_id.is_(None),
            RelationshipEdge.organization_id == org.id,
            RelationshipEdge.relationship_type == "org_membership",
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    edge = RelationshipEdge(
        person_a_id=person.id, person_b_id=None, organization_id=org.id,
        relationship_type="org_membership",
        warmth_tier=taxonomy.warmth_tier("org_membership"),
        cost=taxonomy.edge_cost("org_membership"),
        evidence_snippet=evidence or f"{person.canonical_name} is listed at {org.name}.",
        source_id=source.id if source else None,
        structural=True,
    )
    db.add(edge)
    db.flush()
    if role:
        meta = dict(person.meta or {})
        meta.setdefault("roles", {})[org.name] = role
        person.meta = meta
    return edge


# --- Rule 1: org fan-out cap ----------------------------------------------
def materialize_org_edges(db: Session, org: Organization, members: Iterable[Person],
                          *, source: Optional[Source] = None,
                          relationship_type: str = "",
                          evidence: str = "") -> List[RelationshipEdge]:
    """Turn membership of a SMALL org into pairwise person-person edges.

    RULE 1. An org whose roster exceeds config.MAX_ORG_MEMBERS_FOR_EDGES yields
    NO edges. Ten partners at a VC firm genuinely know each other; 80,000
    Google employees or 17,000 Stanford alumni do not, and materializing that
    clique would make every pair of strangers look two hops apart.

    The org's recorded member_count (the largest roster ever observed for it)
    is authoritative, so a source that happens to list only 5 of Google's
    employees still cannot sneak past the cap.
    """
    members = [m for m in members if m is not None]
    if org is None or len(members) < 2:
        return []

    observed = max(len(members), org.member_count or 0)
    if observed > (org.member_count or 0):
        org.member_count = observed
    if observed > config.MAX_ORG_MEMBERS_FOR_EDGES:
        return []  # mega-hub: membership is recorded, but implies no closeness

    rtype = relationship_type or taxonomy.ORG_TYPE_TO_RELATIONSHIP.get(
        org.type, "colleague")
    note = evidence or f"Both listed on the {org.name} roster."

    edges: List[RelationshipEdge] = []
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            edge = add_edge(db, a, b, rtype, source=source,
                            evidence=note, organization=org)
            if edge is not None:
                edges.append(edge)
    return edges


def people_of_org(db: Session, org: Organization) -> List[Person]:
    """Everyone the graph places at `org` — via membership or a same-firm edge."""
    if org is None:
        return []
    ids = set()
    for edge in db.execute(
        select(RelationshipEdge).where(RelationshipEdge.organization_id == org.id)
    ).scalars():
        ids.add(edge.person_a_id)
        if edge.person_b_id:
            ids.add(edge.person_b_id)
    return [p for p in (db.get(Person, pid) for pid in ids) if p is not None]


def materialize_coinvestor_edges(db: Session, company: Organization
                                 ) -> List[RelationshipEdge]:
    """Two firms' portfolio pages both list `company` => their partners share it.

    Each assertion is independent: firm A's page says A backs the company, and
    firm B's page says B does. Neither page mentions the other. That composition
    supports `shared_portfolio` (tier 4, "back the same portfolio company") and
    NOT `investor_of` (tier 3) — no free source says which partner led the deal,
    or who founded the company.

    Rule 1 applies to the investor set, not the company's staff: if enough firms
    back one company that the combined partner list exceeds the cap, the company
    is a hub (a YC-scale winner) and implies no closeness at all.
    """
    if company is None:
        return []
    investors = (company.meta or {}).get("investors") or {}
    if len(investors) < 2:
        return []   # one assertion is a fact about a firm, not a shared tie

    people: List[Person] = []
    seen = set()
    for firm_key in investors:
        firm = db.execute(
            select(Organization).where(Organization.norm_name == firm_key)
        ).scalar_one_or_none()
        for person in people_of_org(db, firm):
            if person.id not in seen:
                seen.add(person.id)
                people.append(person)

    if len(people) < 2 or len(people) > config.MAX_ORG_MEMBERS_FOR_EDGES:
        return []

    firm_names = ", ".join(v["firm"] for v in investors.values())
    source = None
    for value in investors.values():
        if value.get("source_url"):
            source = get_or_create_source(db, value["source_url"],
                                          title=f"{company.name} investors",
                                          provider="firms")
            break

    edges = []
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            edge = add_edge(db, a, b, "shared_portfolio", source=source,
                            organization=company,
                            evidence=(f"{firm_names} each list {company.name} "
                                      f"({company.norm_name}) in their portfolio."))
            if edge is not None:
                edges.append(edge)
    return edges


def record_coinvestment(db: Session, firms: List[Organization], company: str,
                        source_url: str = "") -> int:
    """Record, on each firm, that it co-invested with the others in `company`.

    An org-level fact, kept even when no person-person edge can be drawn: most
    firms' team pages are JS-rendered, so we often know THAT two firms invested
    together while knowing nobody at one of them. Persisting the assertion means
    the tier-3 edges appear the moment a roster becomes readable, or the moment
    a LinkedIn import supplies the people.
    """
    firms = [f for f in firms if f is not None]
    if len(firms) < 2:
        return 0
    recorded = 0
    for firm in firms:
        others = [f for f in firms if f.id != firm.id]
        meta = dict(firm.meta or {})
        book = dict(meta.get("co_investments") or {})
        for other in others:
            entry = book.setdefault(other.norm_name,
                                    {"firm": other.name, "rounds": []})
            if not any(r.get("company") == company for r in entry["rounds"]):
                entry["rounds"].append({"company": company,
                                        "source_url": source_url})
                recorded += 1
        meta["co_investments"] = book
        firm.meta = meta
    return recorded


def materialize_round_edges(db: Session, firms: List[Organization], *,
                            source: Optional[Source] = None,
                            evidence: str = "") -> List[RelationshipEdge]:
    """Firms named in one round => their partners are co-investors (tier 3).

    Edges run ACROSS firms only. Two partners at the same firm are already
    `same_firm_partner` (tier 2, warmer); adding a tier-3 edge between them would
    be redundant noise.

    Rule 1 applies to the combined partner list: a mega-round with a dozen funds
    is not a room where everyone met.
    """
    firms = [f for f in firms if f is not None]
    if len(firms) < 2:
        return []   # one investor is not a co-investment

    rosters = []
    seen: set = set()
    for firm in firms:
        people = [p for p in people_of_org(db, firm) if p.id not in seen]
        for person in people:
            seen.add(person.id)
        if people:
            rosters.append(people)
    if len(rosters) < 2:
        return []   # we know nobody at the other firm(s), so no person-tie

    if len(seen) > config.MAX_ORG_MEMBERS_FOR_EDGES:
        return []

    edges: List[RelationshipEdge] = []
    for i, left in enumerate(rosters):
        for right in rosters[i + 1:]:
            for a in left:
                for b in right:
                    edge = add_edge(db, a, b, "co_investor", source=source,
                                    evidence=evidence)
                    if edge is not None:
                        edges.append(edge)
    return edges


# --- convenience -----------------------------------------------------------
def clean_person_names(names: Iterable[str]) -> List[str]:
    """Deterministic name-shape filter; dedups on the person key. Never an LLM.

    Role affixes are STRIPPED before judging, not treated as disqualifying: a
    team page that renders "Partner Alex Harris" as one text node still yields
    "Alex Harris". Rejecting such candidates outright silently deleted real
    co-founders — the same failure an LLM filter produced, for the same reason.
    """
    seen, out = set(), []
    for raw in names:
        name = strip_role_affixes((raw or "").strip())
        if not looks_like_person_name(name):
            continue
        key = person_norm_key(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out
