"""Grow Drew's co-investment graph — the VC-native path to more, shorter bridges.

Drew is a GP; his structural reach is the co-investment graph. One Fiat round
(Odynn) already linked him to Bonfire, Precursor and Upfront partners. This walks
his FULL portfolio: for every Fiat portfolio company whose round names Fiat as an
investor, it connects Fiat's partners to that round's co-investors' partners
(tier-2 `co_investor`, Rule-0 clean — the announcement asserts they invested
together). Then, optionally, it walks one step OUT — the co-investors' own rounds
— since those funds sit closer to the wider tech/VC world.

    ./.venv/bin/python scripts/expand_fiat_coinvestment.py \
        --rounds 40 --coinvestors 30 --second-degree --budget 900

Serper/scrape cost is per newly-seen company/firm; the graph is persistent, so a
re-run only pays for what's new. Bounded by a wall-clock budget; fails loudly.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select                       # noqa: E402

from app import config                              # noqa: E402
from app.db import SessionLocal, init_db            # noqa: E402
from app.edges.names import org_norm_key, person_norm_key  # noqa: E402
from app.graph.connect import _adjacency           # noqa: E402
from app.graph.enrich import _search_provider       # noqa: E402
from app.ingest.rounds import (                      # noqa: E402
    ingest_portfolio_rounds, ingest_rounds_for_firm)
from app.models import Organization, Person          # noqa: E402
from app.providers.firms import FirmsProvider        # noqa: E402
from app.providers.funding import FundingProvider    # noqa: E402


def _reach(db, root_id):
    adj, _b, _s, _p = _adjacency(db)
    seen, q = {root_id}, deque([root_id])
    while q:
        n = q.popleft()
        for nb, _e in adj.get(n, []):
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return seen


def _firms_with_partners(db, exclude_key):
    from app.graph import builder
    out = []
    for org in db.execute(select(Organization).where(
            Organization.org_type == "firm")).scalars():
        if org_norm_key(org.name) == exclude_key:
            continue
        if builder.people_of_org(db, org):
            out.append(org.name)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="expand Drew's co-investment graph")
    ap.add_argument("--firm", default=config.FIAT_FIRM_NAME)
    ap.add_argument("--rounds", type=int, default=40,
                    help="max rounds per firm (firm-name search)")
    ap.add_argument("--coinvestors", type=int, default=30,
                    help="max new co-investor rosters scraped per firm")
    ap.add_argument("--second-degree", action="store_true",
                    help="also walk the co-investors' own rounds (one step out)")
    ap.add_argument("--max-second", type=int, default=15,
                    help="how many co-investor firms to expand at 2nd degree")
    ap.add_argument("--budget", type=float, default=900.0, help="wall-clock secs")
    args = ap.parse_args(argv)

    config.MAX_ROUNDS_PER_FIRM = args.rounds
    config.MAX_COINVESTOR_FIRMS = args.coinvestors
    deadline = time.monotonic() + args.budget
    log = lambda m: print(m, file=sys.stderr, flush=True)

    init_db()
    db = SessionLocal()
    try:
        root = db.execute(select(Person).where(
            Person.norm_name == person_norm_key(config.DEMO_SEED_NAME))
        ).scalar_one_or_none()
        before = len(_reach(db, root.id)) if root else 0

        search = _search_provider()
        firms = FirmsProvider(search)
        funding = FundingProvider(search)

        log(f"=== 1st degree: {args.firm} portfolio + rounds ===")
        created = ingest_portfolio_rounds(db, args.firm, firms=firms,
                                          funding=funding, progress=log)
        created += ingest_rounds_for_firm(db, args.firm, firms=firms,
                                          funding=funding, progress=log)

        if args.second_degree and time.monotonic() < deadline:
            log("=== 2nd degree: co-investors' own rounds ===")
            peers = _firms_with_partners(db, org_norm_key(args.firm))
            log(f"  {len(peers)} co-investor firms with rosters; "
                f"expanding up to {args.max_second}")
            for name in peers[: args.max_second]:
                if time.monotonic() >= deadline:
                    log("  ! budget spent — stopping 2nd degree")
                    break
                log(f"  rounds for {name}…")
                created += ingest_rounds_for_firm(db, name, firms=firms,
                                                  funding=funding, progress=log)

        root = db.execute(select(Person).where(
            Person.norm_name == person_norm_key(config.DEMO_SEED_NAME))
        ).scalar_one()
        after = len(_reach(db, root.id))

        # notable funds/people now reachable
        adj, by, _s, _p = _adjacency(db)
        reach = _reach(db, root.id)
        notable = sorted(
            ((len(adj.get(pid, [])), by[pid].canonical_name) for pid in reach
             if pid != root.id and by[pid].wikidata_qid), reverse=True)

        print(f"\nco_investor edges created this run: {created}")
        print(f"reach: {before} -> {after}  (+{after - before})")
        print(f"notable (QID) people reachable: {len(notable)}")
        for deg, name in notable[:15]:
            print(f"   deg {deg:4}  {name}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
