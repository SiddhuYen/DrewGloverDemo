"""Seed Drew Glover's real first degree — the layer that makes this HIS graph.

Two sources, both structural:

1. Fiat Ventures' partners. Fiat's site is a single-page Wix build with no
   /team route, and its homepage interleaves the three partners with quoted
   portfolio founders and testimonial names. NER cannot tell those apart, so
   scraping it would silently invent colleagues. These three partners are
   therefore a VERIFIED MANIFEST, each carrying the source that asserts it —
   not a guess, and not a scrape of a page that asserts nothing.

2. Verified direct connections supplied by Drew, with a public profile URL as
   their stable identity/evidence anchor.

3. Podcast host<->guest edges, scraped live from RSS. An episode asserts that a
   host interviewed a guest; it asserts nothing between two guests. See
   providers/podcasts.py — a feed with no named human host yields no edges.

Drew appeared on DrinksWithAVC (Ep. 37), so its two hosts become tier-1 contacts
and their 36 other guests sit two honest hops away.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from .. import config
from ..graph import builder
from ..providers.podcasts import PodcastProvider

Progress = Optional[Callable[[str], None]]

# Verified from fiat.vc's homepage roster and the firm's own team-spotlight post.
# Each fact names the source that asserts it.
FIAT_PARTNERS = [
    {"name": "Drew Glover",
     "role": "Co-Founder & General Partner",
     "source": "https://www.fiat.vc/post/team-spotlight-drew-glover"},
    {"name": "Alex Harris",
     "role": "Co-Founder & Partner",
     "source": "https://www.fiat.vc"},
    {"name": "Marcos Fernandez",
     "role": "Co-Founder & Partner",
     "source": "https://www.fiat.vc"},
]

# Direct first-degree relationships verified by Drew. Keep these separate from
# scraped social data: the profile URL anchors identity, while Drew's explicit
# confirmation is what asserts the relationship.
DIRECT_CONNECTIONS = [
    {"name": "Bryce Johnson",
     "handle": "@brycent",
     "relationship_type": "instagram_mutual",
     "source": "https://www.instagram.com/brycent/"},
]

# Public, structurally asserted Bryce "Brycent" Johnson relationships. Podcast
# entries connect only the named host(s) to Bryce/the named guest; affiliation
# or article co-mentions are intentionally excluded.
BRYCE_CONNECTIONS = [
    {"name": "Matt Zahab", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/ca/podcast/180-brycent-on-web3-gaming-content-creation-and-loot-bolt/id1559291408?i=1000586926103",
     "evidence": "Matt Zahab interviewed Brycent on CryptoNews Podcast episode 180."},
    {"name": "Kevin Logan Jr.", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/the-immutable-mindset/id1672112862",
     "evidence": "Kevin Logan Jr. co-hosted The Immutable Mindset episode featuring Brycent Johnson."},
    {"name": "Adam Posner", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/the-immutable-mindset/id1672112862",
     "evidence": "Adam Posner co-hosted The Immutable Mindset episode featuring Brycent Johnson."},
    {"name": "Carly Reilly", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/nz/podcast/10-going-mainstream-a-players-perspective-on-web3/id1637105783?i=1000597697613",
     "evidence": "Carly Reilly hosted Between 2 Layers episode 10 featuring Brycent."},
    {"name": "Robbie Ferguson", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/nz/podcast/10-going-mainstream-a-players-perspective-on-web3/id1637105783?i=1000597697613",
     "evidence": "Robbie Ferguson co-hosted Between 2 Layers episode 10 featuring Brycent."},
    {"name": "Cathleen Kuo", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/vesting-with-brycent/id1871729234",
     "evidence": "Brycent interviewed Cathleen Kuo of Opalite Health on Vesting."},
    {"name": "Georgia Witchel", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/vesting-with-brycent/id1871729234",
     "evidence": "Brycent interviewed Georgia Witchel of Mantis Biotech on Vesting."},
    {"name": "Philip Johnston", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/vesting-with-brycent/id1871729234",
     "evidence": "Brycent interviewed Philip Johnston of Starcloud on Vesting."},
    {"name": "Skyler Chan", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/us/podcast/vesting-with-brycent/id1871729234",
     "evidence": "Brycent interviewed Skyler Chan of Gru Space on Vesting."},
]

BRYCE_INTERVIEWED_COMPANIES = [
    {"company": "Opalite Health", "guest": "Cathleen Kuo",
     "founders": ["Cathleen Kuo", "Alex Mehregan"],
     "url": "https://www.ycombinator.com/companies/opalite-health"},
    {"company": "Mantis Biotech", "guest": "Georgia Witchel",
     "founders": ["Georgia Witchel"],
     "url": "https://www.ycombinator.com/companies/mantis"},
    {"company": "Starcloud", "guest": "Philip Johnston",
     "founders": ["Philip Johnston", "Ezra Feilden", "Adi Oltean"],
     "url": "https://www.starcloud.com/"},
    {"company": "GRU Space", "guest": "Skyler Chan",
     "founders": ["Skyler Chan"],
     "url": "https://www.gru.space/team"},
]

ATLAS_CONNECTIONS = [
    {"name": "Drew Glover", "type": "podcast_guest",
     "url": "https://podcasts.apple.com/es/podcast/atlas-berry-m1c/id1843993412?i=1000751794298",
     "evidence": "Drew Glover interviewed Atlas Berry of M1C on VC Uncovered."},
    {"name": "Gopi Rangan", "type": "podcast_guest",
     "url": "https://podcast.sure.ventures/episodes/tsse142-appeal-to-human-side-atlas-berry",
     "evidence": "Gopi Rangan interviewed Atlas Berry on The Sure Shot Entrepreneur."},
    {"name": "Mark Suster", "type": "notable_affiliation",
     "url": "https://www.linkedin.com/posts/atlasberry_early-looks-crazy-late-looks-obvious-activity-7338592942112108544-J0Qc",
     "evidence": "Atlas Berry identifies Mark Suster as one of his most important venture mentors."},
]


def _note(progress: Progress, msg: str) -> None:
    if progress:
        progress(msg)


def seed_fiat(db: Session, progress: Progress = None) -> int:
    """Fiat's partners -> a firm org + pairwise tier-1 `cofounder` edges."""
    source = builder.get_or_create_source(
        db, config.FIAT_SITE_URL, title=f"{config.FIAT_FIRM_NAME} — team",
        provider="verified_seed")

    org = builder.get_or_create_org(
        db, config.FIAT_FIRM_NAME, org_type="firm",
        member_count=len(FIAT_PARTNERS))

    people = []
    for partner in FIAT_PARTNERS:
        person = builder.get_or_create_person(db, partner["name"], is_warm=True)
        if person is None:
            continue
        meta = dict(person.meta or {})
        meta.update({"role": partner["role"], "firm": config.FIAT_FIRM_NAME})
        person.meta = meta
        people.append(person)

    # They founded Fiat together — warmer than a generic same-firm tie.
    edges = builder.materialize_org_edges(
        db, org, people, source=source, relationship_type="cofounder",
        evidence=f"Co-founders and partners at {config.FIAT_FIRM_NAME}.")
    db.commit()
    _note(progress, f"  Fiat: {len(people)} partners, {len(edges)} edges")
    return len(edges)


def seed_direct_connections(db: Session, progress: Progress = None) -> int:
    """Drew's explicitly verified direct contacts. Idempotent and offline."""
    owner = builder.get_or_create_person(db, config.DEMO_SEED_NAME, is_warm=True)
    if owner is None:
        return 0

    created = 0
    for contact in DIRECT_CONNECTIONS:
        person = builder.get_or_create_person(db, contact["name"], is_warm=True)
        if person is None or person.id == owner.id:
            continue
        meta = dict(person.meta or {})
        meta.update({"instagram_handle": contact["handle"],
                     "instagram_url": contact["source"]})
        person.meta = meta
        source = builder.get_or_create_source(
            db, contact["source"], title=f"{contact['name']} on Instagram",
            provider="verified_seed")
        edge = builder.add_edge(
            db, owner, person, contact["relationship_type"], source=source,
            evidence=(f"{owner.canonical_name} and {person.canonical_name} "
                      f"({contact['handle']}) are verified direct Instagram "
                      "connections."))
        if edge is not None:
            created += 1

    db.commit()
    _note(progress, f"  verified direct connections: {created} edges")
    return created


def seed_bryce_connections(db: Session, progress: Progress = None) -> int:
    """Verified public first-degree connections for Bryce Johnson."""
    bryce = builder.get_or_create_person(db, "Bryce Johnson", is_warm=True)
    if bryce is None:
        return 0
    created = 0
    for item in BRYCE_CONNECTIONS:
        person = builder.get_or_create_person(db, item["name"])
        if person is None:
            continue
        source = builder.get_or_create_source(
            db, item["url"], title=item["evidence"], provider="verified_research")
        if builder.add_edge(db, bryce, person, item["type"], source=source,
                            evidence=item["evidence"]):
            created += 1

    for item in BRYCE_INTERVIEWED_COMPANIES:
        source = builder.get_or_create_source(
            db, item["url"], title=f"{item['company']} — founders",
            provider="verified_research")
        org = builder.get_or_create_org(
            db, item["company"], org_type="company",
            member_count=len(item["founders"]))
        founders = []
        for name in item["founders"]:
            founder = builder.get_or_create_person(db, name)
            if founder is None:
                continue
            founders.append(founder)
            builder.add_membership(
                db, founder, org, source=source,
                evidence=f"{name} is a founder of {item['company']}.")
        # The source explicitly identifies these people as co-founders; use the
        # precise relationship instead of generic company colleagues.
        if len(founders) > 1:
            builder.materialize_org_edges(
                db, org, founders, source=source,
                relationship_type="cofounder",
                evidence=f"Co-founders of {item['company']}.")
    db.commit()
    _note(progress, f"  Bryce Johnson: {created} verified public edges")
    return created


def seed_atlas_connections(db: Session, progress: Progress = None) -> int:
    """Identity, firm membership, and asserted public ties for Atlas Berry."""
    atlas = builder.get_or_create_person(db, "Atlas Berry", is_warm=True)
    if atlas is None:
        return 0
    meta = dict(atlas.meta or {})
    meta.update({
        "linkedin_url": "https://www.linkedin.com/in/atlasberry/",
        "instagram_handle": "@atlasberry008",
        "instagram_url": "https://www.instagram.com/atlasberry008/",
        "role": "Founder & General Partner",
        "firm": "Mission One Capital (M1C)",
        "location": "Miami, Florida",
    })
    atlas.meta = meta

    firm_source = builder.get_or_create_source(
        db, "https://www.linkedin.com/in/atlasberry/",
        title="Atlas Berry — LinkedIn", provider="verified_research")
    firm = builder.get_or_create_org(
        db, "Mission One Capital (M1C)", org_type="firm", member_count=1)
    builder.add_membership(
        db, atlas, firm, source=firm_source,
        evidence="Atlas Berry is Founder & General Partner of Mission One Capital (M1C).")

    created = 0
    for item in ATLAS_CONNECTIONS:
        other = builder.get_or_create_person(db, item["name"])
        if other is None or other.id == atlas.id:
            continue
        source = builder.get_or_create_source(
            db, item["url"], title=item["evidence"],
            provider="verified_research")
        if builder.add_edge(db, atlas, other, item["type"], source=source,
                            evidence=item["evidence"]):
            created += 1
    db.commit()
    _note(progress, f"  Atlas Berry: {created} verified public edges")
    return created


def seed_podcasts(db: Session, progress: Progress = None,
                  discover: bool = False) -> int:
    """Host<->guest tier-1 edges from every feed with a named human host.

    With `discover`, the configured feeds are joined by human-hosted VC shows
    found through Apple's podcast directory (Layer C). Each named host is a hub:
    they personally interviewed every guest, so their guests sit two honest hops
    from one another.
    """
    provider = PodcastProvider()
    created = 0

    feeds = list(config.PODCAST_FEEDS)
    if discover:
        known = {f.get("rss") for f in feeds}
        found = [f for f in provider.discover_all() if f["rss"] not in known]
        _note(progress, f"  discovered {len(found)} human-hosted VC feeds")
        feeds.extend(found)

    for feed_cfg in feeds:
        feed = provider.episodes(feed_cfg)
        show = feed.get("show") or feed_cfg.get("show", "")
        hosts = feed.get("hosts") or []

        if not hosts:
            # A corporate host (e.g. Amplitude) cannot be one end of a personal
            # relationship, and guest<->guest is co-occurrence. Emit nothing.
            _note(progress, f"  {show}: no named human host "
                            f"({feed.get('host_author_raw', '')!r}) — 0 edges")
            continue

        host_people = [builder.get_or_create_person(db, h, is_warm=True)
                       for h in hosts]
        host_people = [h for h in host_people if h is not None]

        # Co-hosts genuinely make the show together.
        show_source = builder.get_or_create_source(
            db, feed.get("page", ""), title=show, provider="podcast_rss")
        for i, host_a in enumerate(host_people):
            for host_b in host_people[i + 1:]:
                if builder.add_edge(db, host_a, host_b, "cohost",
                                    source=show_source,
                                    evidence=f"Co-hosts of {show}."):
                    created += 1

        for guest in feed.get("guests", []):
            source = builder.get_or_create_source(
                db, guest.get("episode_url") or feed.get("page", ""),
                title=guest.get("episode_title", ""), provider="podcast_rss")
            person = builder.get_or_create_person(db, guest["guest"])
            if person is None:
                continue

            # "DWAVC Episode 38 | Tae Hea Nahm (Storm Ventures)" — the episode
            # title asserts the guest's firm. Recording it gives enrichment a
            # roster to go fetch.
            if guest.get("org"):
                org = builder.get_or_create_org(db, guest["org"], org_type="firm")
                if org is not None:
                    builder.add_membership(
                        db, person, org, source=source,
                        evidence=(f"{show} introduced {person.canonical_name} "
                                  f"of {org.name}."))
            for host in host_people:
                if host.id == person.id:
                    continue
                edge = builder.add_edge(
                    db, host, person, "podcast_guest", source=source,
                    evidence=(f"{host.canonical_name} interviewed "
                              f"{person.canonical_name} on {show} "
                              f"(“{guest['episode_title']}”)."))
                if edge is not None:
                    created += 1

        _note(progress, f"  {show}: hosts={hosts}, "
                        f"guests={len(feed.get('guests', []))}")

    db.commit()
    _note(progress, f"  podcasts: {created} edges")
    return created


def mark_warm_first_degree(db: Session, root_name: str = "") -> int:
    """Anyone directly connected to Drew is, by definition, in his first degree."""
    from sqlalchemy import select

    from ..graph.enrich import _neighbors
    from ..models import Person
    from ..edges.names import person_norm_key

    root_name = root_name or config.DEMO_SEED_NAME
    root = db.execute(select(Person).where(
        Person.norm_name == person_norm_key(root_name))).scalar_one_or_none()
    if root is None:
        return 0
    root.is_warm = True
    marked = 0
    for person in _neighbors(db, root):
        if not person.is_warm:
            person.is_warm = True
            marked += 1
    db.commit()
    return marked


def seed_drew(db: Session, progress: Progress = None,
              discover: bool = False) -> dict:
    """Build Drew's warm layer. Idempotent — safe to re-run."""
    _note(progress, f"seeding {config.DEMO_SEED_NAME}'s first degree…")
    fiat = seed_fiat(db, progress)
    direct = seed_direct_connections(db, progress)
    bryce = seed_bryce_connections(db, progress)
    atlas = seed_atlas_connections(db, progress)
    podcasts = seed_podcasts(db, progress, discover=discover)
    warm = mark_warm_first_degree(db)
    _note(progress, f"  marked {warm} people as first-degree warm")
    return {"fiat_edges": fiat, "direct_edges": direct, "bryce_edges": bryce,
            "atlas_edges": atlas,
            "podcast_edges": podcasts, "warm_people": warm}
