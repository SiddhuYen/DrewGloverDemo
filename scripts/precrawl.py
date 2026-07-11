"""Optional backbone builder (post-MVP enhancement).

Pre-scrapes the team pages of the most active VC firms so common queries hit a
warm graph instead of paying for on-demand enrichment. Purely additive: the MVP
works without it, because connect() enriches both endpoints on demand.

Requires a search provider (SERPER_API_KEY, else DuckDuckGo) to locate each
firm's /team page. Firms whose roster exceeds the Rule 1 cap are recorded as
organizations but never produce pairwise edges.

    ./.venv/bin/python scripts/precrawl.py --limit 25
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                          # noqa: E402
from app.db import SessionLocal, init_db        # noqa: E402
from app.graph import builder                   # noqa: E402
from app.graph.enrich import _search_provider   # noqa: E402
from app.providers.firms import FirmsProvider   # noqa: E402

TOP_FIRMS = [
    "Fiat Ventures", "Precursor Ventures", "Better Tomorrow Ventures",
    "Storm Ventures", "Hustle Fund", "Upfront Ventures", "Lobby Capital",
    "Bain Capital Ventures", "Costanoa Ventures", "Uncork Capital",
    "Bessemer Venture Partners", "Foundry Group", "Homebrew",
    "Susa Ventures", "Freestyle Capital", "Defy Partners",
    "RareBreed Ventures", "Chapter One Ventures", "Equal Ventures",
    "Basis Set Ventures", "Slow Ventures", "Boldstart Ventures",
    "Amplify Partners", "Craft Ventures", "Wing Venture Capital",
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="pre-scrape VC firm rosters")
    parser.add_argument("--limit", type=int, default=len(TOP_FIRMS))
    args = parser.parse_args(argv)

    init_db()
    db = SessionLocal()
    firms = FirmsProvider(_search_provider())
    total_edges = skipped = 0
    try:
        for firm_name in TOP_FIRMS[: args.limit]:
            roster = firms.roster_for_firm(firm_name)
            members = roster["members"]
            if not members:
                print(f"  {firm_name}: no roster page found")
                continue
            if roster["overflow"]:
                # `members` is truncated to MAX_ROSTER_MEMBERS, so printing its
                # length would understate the roster and read as "40 > 40".
                print(f"  {firm_name}: more than "
                      f"{config.MAX_ORG_MEMBERS_FOR_EDGES} listed — over the "
                      f"Rule 1 cap; no edges")
                skipped += 1
                continue

            org = builder.get_or_create_org(db, firm_name, org_type="firm",
                                            member_count=len(members))
            source = builder.get_or_create_source(
                db, roster["url"], title=f"{firm_name} team", provider="firms")
            people = [builder.get_or_create_person(db, n) for n in members]
            for person in people:
                builder.add_membership(db, person, org, source=source)
            edges = builder.materialize_org_edges(
                db, org, people, source=source,
                relationship_type="same_firm_partner",
                evidence=f"Both listed on the {firm_name} team page.")
            db.commit()
            total_edges += len(edges)
            print(f"  {firm_name}: {len(members)} partners, {len(edges)} edges")

        # --- Layer D: portfolios -> co-investment ---------------------------
        print("\nportfolios:")
        companies = 0
        for firm_name in TOP_FIRMS[: args.limit]:
            firm = builder.get_or_create_org(db, firm_name, org_type="firm",
                                             allow_create=False)
            if firm is None:
                continue
            book = firms.portfolio_for_firm(firm_name)
            if not book["companies"]:
                print(f"  {firm_name}: no portfolio page read")
                continue
            source = builder.get_or_create_source(
                db, book["url"], title=f"{firm_name} portfolio", provider="firms")
            for entry in book["companies"]:
                company = builder.get_or_create_company(db, entry["name"],
                                                        entry["domain"])
                if company is not None and builder.record_investment(
                        db, company, firm, source=source):
                    companies += 1
            db.commit()
            print(f"  {firm_name}: {len(book['companies'])} companies")

        # A company backed by two firms bridges them at tier 4.
        from app.models import Organization
        from sqlalchemy import select as _select
        co_edges = shared = 0
        for company in db.execute(
                _select(Organization).where(Organization.type == "company")).scalars():
            made = builder.materialize_coinvestor_edges(db, company)
            if made:
                shared += 1
                co_edges += len(made)
        db.commit()

        print(f"\ndone — {total_edges} roster edges; {skipped} firm(s) skipped by "
              f"Rule 1\n       {companies} investments; {co_edges} shared-portfolio "
              f"edges across {shared} co-backed companies")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
