"""Structured enrichment for ONE person — the shared engine behind both endpoints.

`enrich_person(db, name)` pulls only sources that structurally assert a tie:

    Wikidata     employer / member-of / chaired-board claims, then the reverse
                 lookup of who else holds that claim (with a SPARQL COUNT first,
                 so a mega-hub is skipped under Rule 1 before its roster loads)
    SEC EDGAR    fellow Form 4 filers at the same issuer (board/insider)
    OpenCorp     fellow registered officers of the same company
    Firm roster  the team page of a firm the person belongs to

Every neighbour arrives attached to an ORG. We record membership, then let
builder.materialize_org_edges decide — under the Rule 1 cap — whether that
membership implies person-person edges. No path is ever built from a snippet.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from .. import config
from ..models import Person
from ..providers.edgar import EdgarProvider
from ..providers.firms import FirmsProvider
from ..providers.opencorporates import OpenCorporatesProvider
from ..providers.openalex import OpenAlexProvider
from ..providers.podcasts import PodcastProvider
from ..providers.propublica import ProPublicaProvider
from ..providers.wikidata import WikidataProvider
from ..providers.wikipedia import WikipediaProvider
from . import builder
from .bridge import rank_frontier

Progress = Optional[Callable[[str], None]]


def _note(progress: Progress, msg: str) -> None:
    if progress:
        progress(msg)


def _search_provider():
    """Serper when configured, else DuckDuckGo. Used only to locate roster pages."""
    from ..providers.duckduckgo import DuckDuckGoProvider
    from ..providers.serper import SerperProvider

    serper = SerperProvider()
    return serper if serper.available() else DuckDuckGoProvider()


class Enricher:
    def __init__(self) -> None:
        self.wikipedia = WikipediaProvider()
        self.wikidata = WikidataProvider()
        self.edgar = EdgarProvider()
        self.opencorp = OpenCorporatesProvider()
        self.firms = FirmsProvider(_search_provider())
        self.podcasts = PodcastProvider()
        self.openalex = OpenAlexProvider()
        self.propublica = ProPublicaProvider()

    # --- one org's roster -> membership + (maybe) pairwise edges ----------
    def _absorb_org(self, db: Session, subject: Person, org_name: str,
                    org_type: str, member_names: List[str], member_count: int,
                    relationship_type: str, source_url: str, evidence: str,
                    source_title: str = "") -> int:
        """Record an org and its roster, then materialize edges under Rule 1.

        Returns the number of person-person edges created (0 for a mega-hub).
        """
        clean = builder.clean_person_names(member_names)
        if not clean:
            return 0

        org = builder.get_or_create_org(
            db, org_name, org_type=org_type,
            # The count from the provider is authoritative; it may exceed the
            # names we actually pulled (we cap roster fetches).
            member_count=max(member_count, len(clean) + 1))
        if org is None:
            return 0

        source = builder.get_or_create_source(
            db, source_url, title=source_title or org_name, provider="structured")

        people = [subject]
        for name in clean:
            person = builder.get_or_create_person(db, name)
            if person is not None and person.id != subject.id:
                people.append(person)

        # Give every member their firm, so a later enrichment pass can fetch
        # that firm's roster and keep walking outward. Membership rows carry no
        # person_b and are never traversed.
        if org.type == "firm":
            for person in people:
                builder.add_membership(db, person, org, source=source,
                                       evidence=f"Listed on the {org.name} roster.")

        edges = builder.materialize_org_edges(
            db, org, people, source=source,
            relationship_type=relationship_type, evidence=evidence)
        return len(edges)

    # --- providers ---------------------------------------------------------
    def _from_wikidata(self, db: Session, subject: Person, progress: Progress) -> int:
        qid = subject.wikidata_qid or self.wikipedia.qid_for_name(subject.canonical_name)
        if not qid or not self.wikidata.is_human(qid):
            return 0
        if not subject.wikidata_qid:
            subject.wikidata_qid = qid

        created = 0
        for org in self.wikidata.orgs_for_person(qid):
            prop, org_qid = org["prop"], org["org_qid"]
            count = self.wikidata.org_member_count(org_qid, prop)

            # A shared EMPLOYER is never a tie, at any size — and the size cap
            # cannot police it, because the count is of Wikidata-listed humans,
            # not headcount (PayPal: 24, Reddit: 4). Record the membership only.
            employer_only = not org.get("materialize_edges", True)

            # A `member of` target must be a BODY, not a rich-list or a union.
            # "The World's Billionaires" is typed `order`; SAG-AFTRA is a
            # `political coalition`. Either would assert that Trump, Musk and
            # Buffett "served on the same board".
            not_a_body = (not employer_only
                          and not self.wikidata.org_is_board_like(org_qid))
            if not_a_body:
                _note(progress, f"    {org['org_name']}: not a body "
                                f"({', '.join(self.wikidata.org_kinds(org_qid)) or 'unknown'})"
                                f" — membership only, no edges")

            if employer_only or not_a_body or count > config.MAX_ORG_MEMBERS_FOR_EDGES:
                # Rule 1 removes the CLOSENESS, not the fact. "Sam Altman works
                # at OpenAI" is true and useful — it corroborates his identity on
                # a podcast — even though his 79 colleagues are not his contacts.
                mega = builder.get_or_create_org(
                    db, org["org_name"], org_type="company", member_count=count)
                if mega is not None:
                    source = builder.get_or_create_source(
                        db, f"https://www.wikidata.org/wiki/{org_qid}",
                        title=org["org_name"], provider="structured")
                    builder.add_membership(db, subject, mega, source=source)
                if employer_only:
                    _note(progress, f"    {org['org_name']}: employer — a shared "
                                    f"employer is not a relationship (no edges)")
                elif not not_a_body:
                    _note(progress, f"    {org['org_name']}: {count} members > "
                                    f"Rule 1 cap (no edges)")
                continue
            members = self.wikidata.org_members(org_qid, prop)
            if len(members) < 2:
                continue
            created += self._absorb_org(
                db, subject, org["org_name"],
                org_type="firm" if "ventures" in org["org_name"].lower() else "company",
                member_names=members, member_count=count,
                relationship_type=org["relationship_type"],
                source_url=f"https://www.wikidata.org/wiki/{org_qid}",
                source_title=org["org_name"],
                evidence=(f"Wikidata records both as {org['phrase']} "
                          f"{org['org_name']}."),
            )
        created += self._from_wikidata_family(db, subject, qid, progress)
        created += self._from_wikidata_cofounders(db, subject, qid, progress)
        created += self._from_wikidata_entertainment(db, subject, qid, progress)
        if created:
            _note(progress, f"    wikidata: {created} edges")
        return created

    def _from_wikidata_family(self, db: Session, subject: Person, qid: str,
                              progress: Progress) -> int:
        """Direct family claims -> `family_member` edges.

        Each relative is resolved BY QID, so a homonym is never merged: the claim
        target is a specific Wikidata person, not a name to re-search.
        """
        created = 0
        source = builder.get_or_create_source(
            db, f"https://www.wikidata.org/wiki/{qid}",
            title=subject.canonical_name, provider="structured")
        for rel in self.wikidata.family_for_person(qid):
            relative = builder.get_or_create_person(
                db, rel["person_name"], qid=rel["person_qid"])
            if relative is None or relative.id == subject.id:
                continue
            edge = builder.add_edge(
                db, subject, relative, "family_member", source=source,
                evidence=(f"Wikidata records that {subject.canonical_name} "
                          f"{rel['phrase']} {relative.canonical_name}."))
            if edge is not None:
                created += 1
        if created:
            _note(progress, f"    family: {created} edges")
        return created

    def _from_wikidata_cofounders(self, db: Session, subject: Person, qid: str,
                                  progress: Progress) -> int:
        """Co-founders of an org this person founded -> `cofounder` edges (tier 1).

        Resolved BY QID, so identity is exact. Founding together is one of the
        strongest ties there is, and it is clique-free (few founders per org),
        so no Rule 1 cap is needed.
        """
        created = 0
        source = builder.get_or_create_source(
            db, f"https://www.wikidata.org/wiki/{qid}",
            title=subject.canonical_name, provider="structured")
        for rel in self.wikidata.cofounders_for_person(qid):
            other = builder.get_or_create_person(
                db, rel["person_name"], qid=rel["person_qid"])
            if other is None or other.id == subject.id:
                continue
            edge = builder.add_edge(
                db, subject, other, "cofounder", source=source,
                evidence=(f"Wikidata records {subject.canonical_name} and "
                          f"{other.canonical_name} as co-founders of the same "
                          f"organisation."))
            if edge is not None:
                created += 1
        if created:
            _note(progress, f"    cofounders: {created} edges")
        return created

    def _from_wikidata_entertainment(self, db: Session, subject: Person,
                                     qid: str, progress: Progress) -> int:
        """Co-appearance ties for entertainers/athletes: same film cast, same
        band, same (small) sports team. Each is resolved BY QID and is a real,
        bounded co-appearance — Drew's Instagram network includes actors,
        musicians and athletes, so these are their bridges into that world.
        """
        source = builder.get_or_create_source(
            db, f"https://www.wikidata.org/wiki/{qid}",
            title=subject.canonical_name, provider="structured")
        specs = (
            ("co_star", self.wikidata.costars_for_person,
             "appeared in the same film or show as"),
            ("bandmate", self.wikidata.bandmates_for_person,
             "played in the same band as"),
            ("teammate", self.wikidata.teammates_for_person,
             "played on the same sports team as"),
        )
        created = 0
        for rtype, fetch, phrase in specs:
            n = 0
            for rel in fetch(qid):
                other = builder.get_or_create_person(
                    db, rel["person_name"], qid=rel["person_qid"])
                if other is None or other.id == subject.id:
                    continue
                edge = builder.add_edge(
                    db, subject, other, rtype, source=source,
                    evidence=(f"Wikidata records that {subject.canonical_name} "
                              f"{phrase} {other.canonical_name}."))
                if edge is not None:
                    n += 1
            if n:
                _note(progress, f"    {rtype}: {n} edges")
            created += n
        return created

    def _from_edgar(self, db: Session, subject: Person, progress: Progress) -> int:
        rows = self.edgar.board_colleagues(subject.canonical_name)
        if not rows:
            return 0
        by_org: dict = {}
        for row in rows:
            by_org.setdefault(row["org"], []).append(row)

        created = 0
        for org_name, group in by_org.items():
            head = group[0]
            created += self._absorb_org(
                db, subject, org_name, org_type="company",
                member_names=[r["name"] for r in group],
                member_count=head["member_count"],
                relationship_type="board_member",
                source_url=head["source_url"], source_title=org_name,
                evidence=head["evidence"],
            )
        if created:
            _note(progress, f"    edgar: {created} edges")
        return created

    def _from_opencorporates(self, db: Session, subject: Person,
                             progress: Progress) -> int:
        rows = self.opencorp.officer_colleagues(subject.canonical_name)
        if not rows:
            return 0
        by_org: dict = {}
        for row in rows:
            by_org.setdefault(row["org"], []).append(row)

        created = 0
        for org_name, group in by_org.items():
            head = group[0]
            created += self._absorb_org(
                db, subject, org_name, org_type="company",
                member_names=[r["name"] for r in group],
                member_count=head["member_count"],
                relationship_type=head["relationship_type"],
                source_url=head["source_url"], source_title=org_name,
                evidence=head["evidence"],
            )
        if created:
            _note(progress, f"    opencorporates: {created} edges")
        return created

    def _from_openalex(self, db: Session, subject: Person,
                       progress: Progress) -> int:
        """Co-authors of the same paper -> `coauthor` edges (tier 2).

        Only fires for a person whose OpenAlex author corroborates a known org,
        so a VC is never merged with an academic namesake.
        """
        known = [o.name for o in _orgs_of(db, subject)]
        rows = self.openalex.coauthors(subject.canonical_name, known_orgs=known)
        created = 0
        for row in rows:
            other = builder.get_or_create_person(db, row["name"])
            if other is None or other.id == subject.id:
                continue
            source = builder.get_or_create_source(
                db, row["source_url"], title="OpenAlex", provider="structured")
            edge = builder.add_edge(db, subject, other, "coauthor",
                                    source=source, evidence=row["evidence"])
            if edge is not None:
                created += 1
        if created:
            _note(progress, f"    openalex: {created} coauthor edges")
        return created

    def _from_propublica(self, db: Session, subject: Person,
                         progress: Progress) -> int:
        """Fellow officers/directors of a nonprofit -> `board_member` (tier 2).

        Corroborated by a known org, and materialised under the Rule 1 cap.
        """
        known = [o.name for o in _orgs_of(db, subject)]
        rows = self.propublica.board_colleagues(subject.canonical_name,
                                                org_hints=known)
        if not rows:
            return 0
        by_org: dict = {}
        for row in rows:
            by_org.setdefault(row["org"], []).append(row)

        created = 0
        for org_name, group in by_org.items():
            head = group[0]
            created += self._absorb_org(
                db, subject, org_name, org_type="nonprofit",
                member_names=[r["name"] for r in group],
                member_count=head["member_count"],
                relationship_type="board_member",
                source_url=head["source_url"], source_title=org_name,
                evidence=head["evidence"])
        if created:
            _note(progress, f"    propublica: {created} board edges")
        return created

    def _from_podcasts(self, db: Session, subject: Person,
                       progress: Progress) -> int:
        """Shows this person was a GUEST on -> tier-1 edges to each named host.

        Person-first. Seeding walks known feeds; without this silo a prominent
        figure is invisible: Sam Altman yields three Wikidata edges (Reddit) and
        nothing else, while Harry Stebbings' interview of him goes unread.

        The person's known organisations are passed as corroboration, so an
        episode titled "Drew Glover" on a local news show — a different Drew
        Glover — cannot merge into his node.
        """
        known = [o.name for o in _orgs_of(db, subject)]
        appearances = self.podcasts.appearances(subject.canonical_name,
                                                known_orgs=known)
        created = 0
        for appearance in appearances:
            source = builder.get_or_create_source(
                db, appearance["episode_url"],
                title=appearance["episode_title"], provider="podcast_rss")

            if appearance.get("org"):
                org = builder.get_or_create_org(db, appearance["org"],
                                                org_type="firm")
                if org is not None:
                    builder.add_membership(db, subject, org, source=source)

            for host_name in appearance["hosts"]:
                host = builder.get_or_create_person(db, host_name)
                if host is None or host.id == subject.id:
                    continue
                edge = builder.add_edge(
                    db, host, subject, "podcast_guest", source=source,
                    evidence=(f"{host.canonical_name} interviewed "
                              f"{subject.canonical_name} on "
                              f"{appearance['show']} "
                              f"(“{appearance['episode_title']}”)."))
                if edge is not None:
                    created += 1

            # Make the host the HUB they really are: ingest their show's whole
            # guest list, not just this one episode. This is what bridges a
            # famous target into the reachable graph — Joe Rogan interviewed both
            # Elon Musk and Sam Altman, so pulling JRE's roster links Musk's
            # island to Drew's world in one hop. Each guest is a separately
            # asserted interview, so Rule 0 holds; done once per feed, cached.
            created += self._ingest_host_feed(db, appearance, progress)

        if created:
            _note(progress, f"    podcasts: {created} edges across "
                            f"{len(appearances)} appearances")
        return created

    def _ingest_host_feed(self, db: Session, appearance: dict,
                          progress: Progress) -> int:
        """host <-> every guest of the host's show. One cached feed parse."""
        rss = appearance.get("rss")
        if not rss:
            return 0
        feed = self.podcasts.episodes({"show": appearance.get("show", ""),
                                       "rss": rss, "page": appearance.get("page", "")})
        hosts = [builder.get_or_create_person(db, h) for h in feed.get("hosts", [])]
        hosts = [h for h in hosts if h is not None]
        if not hosts:
            return 0

        created = 0
        for guest in feed.get("guests", [])[: config.MAX_HOST_FEED_GUESTS]:
            source = builder.get_or_create_source(
                db, guest.get("episode_url") or "",
                title=guest.get("episode_title", ""), provider="podcast_rss")
            person = builder.get_or_create_person(db, guest["guest"])
            if person is None:
                continue
            for host in hosts:
                if host.id == person.id:
                    continue
                edge = builder.add_edge(
                    db, host, person, "podcast_guest", source=source,
                    evidence=(f"{host.canonical_name} interviewed "
                              f"{person.canonical_name} on {feed.get('show', '')} "
                              f"(“{guest['episode_title']}”)."))
                if edge is not None:
                    created += 1
        if created:
            _note(progress, f"    host feed {feed.get('show','')[:30]}: "
                            f"{created} guest edges")
        return created

    def _absorb_roster(self, db: Session, subject: Person, roster: dict,
                       progress: Progress) -> int:
        firm = roster.get("firm") or ""
        members = roster.get("members") or []
        if not firm or len(members) < 2:
            return 0
        if roster.get("overflow"):
            _note(progress, f"    skip {firm} roster (over Rule 1 cap)")
            # Still record membership: the fact is true, the closeness is not.
            org = builder.get_or_create_org(db, firm, org_type="firm",
                                            member_count=len(members) + 1)
            source = builder.get_or_create_source(db, roster["url"],
                                                  title=f"{firm} team",
                                                  provider="firms")
            builder.add_membership(db, subject, org, source=source)
            return 0
        return self._absorb_org(
            db, subject, firm, org_type="firm",
            member_names=members, member_count=len(members) + 1,
            relationship_type="same_firm_partner",
            source_url=roster["url"], source_title=f"{firm} team",
            evidence=f"Both listed on the {firm} team page.",
        )

    def _from_person_firms(self, db: Session, subject: Person,
                           progress: Progress) -> int:
        """Find the firms whose ROSTER NAMES this person, then absorb them.

        This is the layer that lets an arbitrary VC gain colleagues. Without it
        a roster can only be reached firm-first (via precrawl), so a podcast
        guest like Charles Hudson stays a lone node with no firm at all.
        """
        created = 0
        for roster in self.firms.find_person_firms(subject.canonical_name):
            created += self._absorb_roster(db, subject, roster, progress)
        if created:
            _note(progress, f"    person->firm rosters: {created} edges")
        return created

    def _from_firm_rosters(self, db: Session, subject: Person,
                           progress: Progress) -> int:
        """Team pages of firms this person ALREADY belongs to in the graph
        (from a Wikidata employer claim, or a podcast title's parenthetical).

        Skips any firm whose roster was already absorbed for this person. The
        edge dedup key includes the source URL, so re-absorbing the same firm
        from a different page ("/team" vs "/team-members/sheel-mohnot") would
        write a second row for every colleague pair.
        """
        created = 0
        for org in _orgs_of(db, subject):
            if org.type != "firm" or _has_firm_edges(db, subject, org):
                continue
            roster = self.firms.roster_for_firm(org.name)
            if not roster.get("members"):
                continue
            created += self._absorb_roster(db, subject, roster, progress)
            if created and len(_orgs_of(db, subject)) >= config.MAX_FIRMS_PER_PERSON:
                break
        if created:
            _note(progress, f"    known-firm rosters: {created} edges")
        return created

    # --- public ------------------------------------------------------------
    def enrich_person(self, db: Session, name: str, *, progress: Progress = None,
                      force: bool = False) -> Optional[Person]:
        """Pull structured sources for one person and persist the edges.

        Idempotent: a person already marked `enriched` is skipped unless forced,
        so a second connect() reuses the graph instead of re-fetching.
        """
        subject = builder.get_or_create_person(db, name)
        if subject is None:
            return None
        if subject.enriched and not force:
            return subject

        _note(progress, f"  enriching {subject.canonical_name}…")
        total = 0
        # Ordered cheapest/most-authoritative first. `_from_firm_rosters` runs
        # last so it can use any firm the earlier layers attached.
        # Wikidata first: it attaches the person's organisations, which the
        # podcast silo then uses to corroborate identity against homonyms.
        # OpenAlex and ProPublica run after the org-attaching layers, because
        # both corroborate identity against the person's known organisations.
        for step in (self._from_wikidata, self._from_edgar,
                     self._from_opencorporates, self._from_openalex,
                     self._from_propublica, self._from_person_firms,
                     self._from_firm_rosters, self._from_podcasts):
            try:
                total += step(db, subject, progress)
            except Exception as exc:  # one dead provider must not sink the run
                _note(progress, f"    {step.__name__} failed: {exc}")

        subject.enriched = 1
        db.commit()
        _note(progress, f"  {subject.canonical_name}: {total} structural edges")
        return subject

    def enrich_neighborhood(self, db: Session, name: str, depth: int = 1,
                            progress: Progress = None, *,
                            opposite_component: "set | None" = None,
                            deadline: "float | None" = None,
                            prefer_notable: bool = False,
                            fanout: "int | None" = None) -> Optional[Person]:
        """Enrich `name`, then walk its neighbourhood outward, hop by hop.

        A multi-hop BFS (ArtemisV2's `expand_graph` shape): each hop enriches a
        ranked slice of the people discovered at the previous hop. The ranking is
        the FAME GRADIENT (see graph/bridge.py) — expand the least-notable
        eligible people first, because a warm intro runs toward an ordinary
        reachable network, not up into more celebrities. Reuse is free: an
        already-`enriched` node is skipped, so a deeper run only pays for the new
        hop.

        Bounded by fan-out per hop and a shared wall-clock deadline. On expiry we
        report what was skipped — a silently thin graph reads as "no path" when
        the truth is "we stopped looking".
        """
        subject = self.enrich_person(db, name, progress=progress)
        if subject is None or depth <= 1:
            return subject

        if deadline is None:
            deadline = time.monotonic() + config.ENRICH_TIME_BUDGET_S
        fanout = fanout or config.ENRICH_FRONTIER_FANOUT

        enriched_here, skipped_total = 0, 0
        frontier = [subject]
        for hop in range(1, depth):
            discovered, seen_ids = [], set()
            for node in frontier:
                for neighbour, edge in _neighbors_with_edges(db, node):
                    if not neighbour.enriched and neighbour.id not in seen_ids:
                        seen_ids.add(neighbour.id)
                        discovered.append((neighbour, edge))
            if not discovered:
                break

            # Surface the cheap, decisive signals BEFORE the notability check
            # (one Wikipedia lookup per candidate, wasteful on a frontier of
            # hundreds): a meeting node, or one reached through a podcast, always
            # ranks ahead of the fame gradient. Sort by those, then rank a
            # bounded slice.
            def _cheap_key(pair):
                person, edge = pair
                meets = bool(opposite_component and person.id in opposite_component)
                via_podcast = (edge is not None
                               and edge.relationship_type in ("podcast_guest",
                                                              "cohost"))
                return (0 if meets else 1, 0 if via_podcast else 1)
            discovered.sort(key=_cheap_key)
            ranked = rank_frontier(db, discovered[: config.ENRICH_MAX_FRONTIER],
                                   opposite_component=opposite_component,
                                   prefer_notable=prefer_notable)
            budgeted = ranked[: fanout]

            next_frontier = []
            for person in budgeted:
                if time.monotonic() > deadline:
                    skipped_total += len(ranked) - len(next_frontier)
                    _note(progress, f"    budget spent at hop {hop}; "
                                    f"{skipped_total} neighbours left unexplored")
                    return subject
                try:
                    self.enrich_person(db, person.canonical_name, progress=progress)
                    enriched_here += 1
                    next_frontier.append(person)
                except Exception as exc:
                    _note(progress, f"    frontier {person.canonical_name} "
                                    f"failed: {exc}")
            skipped_total += max(0, len(ranked) - len(budgeted))
            frontier = next_frontier

        if enriched_here:
            _note(progress, f"    expanded {enriched_here} neighbours of "
                            f"{subject.canonical_name} over {depth - 1} hop(s)"
                            + (f"; {skipped_total} left unexplored"
                               if skipped_total else ""))
        return subject


# --- small DB helpers ------------------------------------------------------
def _has_firm_edges(db: Session, person: Person, org) -> bool:
    """True when this person already has same-firm colleagues at `org`."""
    from sqlalchemy import select

    from ..models import RelationshipEdge

    return db.execute(
        select(RelationshipEdge.id).where(
            RelationshipEdge.organization_id == org.id,
            RelationshipEdge.relationship_type == "same_firm_partner",
            RelationshipEdge.person_b_id.isnot(None),
            (RelationshipEdge.person_a_id == person.id)
            | (RelationshipEdge.person_b_id == person.id),
        ).limit(1)
    ).first() is not None


def _orgs_of(db: Session, person: Person):
    from sqlalchemy import select

    from ..models import Organization, RelationshipEdge

    org_ids = {
        e.organization_id for e in db.execute(
            select(RelationshipEdge).where(
                (RelationshipEdge.person_a_id == person.id)
                | (RelationshipEdge.person_b_id == person.id)
            )
        ).scalars() if e.organization_id
    }
    if not org_ids:
        return []
    return list(db.execute(
        select(Organization).where(Organization.id.in_(org_ids))).scalars())


def _neighbors(db: Session, person: Person) -> List[Person]:
    """Direct neighbours, warmest (lowest-cost) edge first."""
    return [p for p, _edge in _neighbors_with_edges(db, person)]


def _neighbors_with_edges(db: Session, person: Person):
    """(neighbour, arrival_edge) pairs, warmest edge first.

    The arrival edge's type is what tells the expansion ranker that a neighbour
    was reached through a podcast — the signal that a famous host (Rogan, Theo
    Von) is a bridge, which the fame gradient alone would wrongly demote."""
    from sqlalchemy import select

    from ..models import RelationshipEdge

    edges = list(db.execute(
        select(RelationshipEdge).where(
            (RelationshipEdge.person_a_id == person.id)
            | (RelationshipEdge.person_b_id == person.id)
        ).order_by(RelationshipEdge.cost)
    ).scalars())

    seen, out = {person.id}, []
    for edge in edges:
        other_id = (edge.person_b_id if edge.person_a_id == person.id
                    else edge.person_a_id)
        if not other_id or other_id in seen:
            continue
        seen.add(other_id)
        other = db.get(Person, other_id)
        if other is not None:
            out.append((other, edge))
    return out


_enricher: Optional[Enricher] = None


def get_enricher() -> Enricher:
    global _enricher
    if _enricher is None:
        _enricher = Enricher()
    return _enricher


def enrich_person(db: Session, name: str, **kw) -> Optional[Person]:
    return get_enricher().enrich_person(db, name, **kw)


def enrich_neighborhood(db: Session, name: str, depth: int = 1, **kw) -> Optional[Person]:
    return get_enricher().enrich_neighborhood(db, name, depth=depth, **kw)
