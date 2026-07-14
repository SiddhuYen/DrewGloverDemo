"""Ingest funding rounds -> co-investor edges (tier 3).

For a firm we already know partners for, find the rounds whose announcement
names it as an investor, resolve the OTHER investors named in the same round,
fetch their team pages, and connect the partners across firms.

This is the layer that reaches Drew's actual peers. Fiat Ventures' portfolio
page shares no company with our corpus, but its rounds name Bonfire Ventures,
Link Ventures, Gemini Ventures and PBJ Capital — firms whose rosters we can then
read, giving Drew tier-3 ties to real co-investors.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from .. import config
from ..edges.names import org_norm_key
from ..graph import builder
from ..providers.firms import FirmsProvider
from ..providers.funding import FundingProvider

Progress = Optional[Callable[[str], None]]


def _note(progress: Progress, msg: str) -> None:
    if progress:
        progress(msg)


def _ensure_firm_with_partners(db: Session, firm_name: str,
                               firms: FirmsProvider, progress: Progress):
    """Resolve a firm to an org that has partners, scraping its roster if new."""
    org = builder.get_or_create_org(db, firm_name, org_type="firm")
    if org is None:
        return None
    if builder.people_of_org(db, org):
        return org

    roster = firms.roster_for_firm(firm_name)
    members = roster.get("members") or []
    if not members or roster.get("overflow"):
        return org  # known, but nobody to connect (unreadable or a mega-hub)

    source = builder.get_or_create_source(db, roster["url"],
                                          title=f"{firm_name} team",
                                          provider="firms")
    people = [builder.get_or_create_person(db, n) for n in members]
    for person in people:
        builder.add_membership(db, person, org, source=source)
    builder.materialize_org_edges(
        db, org, people, source=source, relationship_type="same_firm_partner",
        evidence=f"Both listed on the {firm_name} team page.")
    _note(progress, f"      + {firm_name}: {len(members)} partners")
    return org


def ingest_rounds_for_firm(db: Session, firm_name: str, *,
                           firms: FirmsProvider, funding: FundingProvider,
                           progress: Progress = None) -> int:
    """Read `firm_name`'s rounds and connect its partners to its co-investors."""
    anchor = builder.get_or_create_org(db, firm_name, org_type="firm",
                                       allow_create=False)
    if anchor is None or not builder.people_of_org(db, anchor):
        return 0  # we know nobody here, so no person-level tie can be drawn

    budget = {"new_firms": 0}
    created = 0
    for round_ in funding.rounds_for_firm(firm_name):
        created += _materialize_round(db, anchor, round_, firms, budget, progress)
    db.commit()
    return created


def _materialize_round(db: Session, anchor, round_, firms: FirmsProvider,
                       budget: dict, progress: Progress) -> int:
    """Connect the anchor's partners to the round's co-investors' partners.

    `budget` is a shared {"new_firms": int} counter so a run can't scrape an
    unbounded number of new co-investor rosters.
    """
    firm_name = anchor.name
    others = [n for n in round_["investors"]
              if org_norm_key(n) != org_norm_key(firm_name)]
    if not others:
        return 0
    orgs = [anchor]
    for other in others:
        if budget["new_firms"] >= config.MAX_COINVESTOR_FIRMS:
            break
        existing = builder.get_or_create_org(db, other, org_type="firm",
                                             allow_create=False)
        if existing is None or not builder.people_of_org(db, existing):
            budget["new_firms"] += 1
        org = _ensure_firm_with_partners(db, other, firms, progress)
        if org is not None:
            orgs.append(org)

    company = round_.get("company") or "the round"
    amount = round_.get("amount") or ""
    source = builder.get_or_create_source(
        db, round_["source_url"], title=f"{company} funding round",
        provider="funding")
    evidence = (f"Both firms invested in {company}"
                f"{f' ({amount})' if amount else ''}: “{round_['evidence']}”")
    builder.record_coinvestment(db, orgs, company,
                                source_url=round_["source_url"])
    edges = builder.materialize_round_edges(db, orgs, source=source,
                                            evidence=evidence)
    unreadable = [o.name for o in orgs if not builder.people_of_org(db, o)]
    note = f"    {company}: {len(orgs)} firms, {len(edges)} edges"
    if unreadable:
        note += f"  (no roster for: {', '.join(unreadable)})"
    _note(progress, note)
    return len(edges)


def ingest_portfolio_rounds(db: Session, firm_name: str, *,
                            firms: FirmsProvider, funding: FundingProvider,
                            progress: Progress = None) -> int:
    """Walk a firm's PORTFOLIO company-by-company, and for each company whose
    round names the firm as an investor, connect the firm's partners to the
    round's co-investors. Recovers rounds that firm-name search misses because
    the announcement leads with the company, not the investor.
    """
    anchor = builder.get_or_create_org(db, firm_name, org_type="firm",
                                       allow_create=False)
    if anchor is None or not builder.people_of_org(db, anchor):
        return 0
    target = org_norm_key(firm_name)
    portfolio = firms.portfolio_for_firm(firm_name)
    companies = portfolio.get("companies") or []
    _note(progress, f"  portfolio: {len(companies)} companies for {firm_name}")

    budget = {"new_firms": 0}
    created = 0
    for company in companies:
        name = company.get("name") if isinstance(company, dict) else company
        if not name:
            continue
        round_ = funding.round_for_company(name, target_firm_key=target)
        if not round_:
            continue
        keys = {org_norm_key(n) for n in round_["investors"]}
        if target not in keys or len(keys) < 2:
            continue          # anchor not named in this round, or no co-investor
        created += _materialize_round(db, anchor, round_, firms, budget, progress)
    db.commit()
    return created


def ingest_rounds(db: Session, firm_names: List[str],
                  progress: Progress = None) -> int:
    from ..graph.enrich import _search_provider

    search = _search_provider()
    firms = FirmsProvider(search)
    funding = FundingProvider(search)
    total = 0
    for firm_name in firm_names:
        _note(progress, f"  rounds for {firm_name}…")
        total += ingest_rounds_for_firm(db, firm_name, firms=firms,
                                        funding=funding, progress=progress)
    return total
