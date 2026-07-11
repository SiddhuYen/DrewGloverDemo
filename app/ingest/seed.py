"""Seed Drew Glover's real first degree — the layer that makes this HIS graph.

Two sources, both structural:

1. Fiat Ventures' partners. Fiat's site is a single-page Wix build with no
   /team route, and its homepage interleaves the three partners with quoted
   portfolio founders and testimonial names. NER cannot tell those apart, so
   scraping it would silently invent colleagues. These three partners are
   therefore a VERIFIED MANIFEST, each carrying the source that asserts it —
   not a guess, and not a scrape of a page that asserts nothing.

2. Podcast host<->guest edges, scraped live from RSS. An episode asserts that a
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
    podcasts = seed_podcasts(db, progress, discover=discover)
    warm = mark_warm_first_degree(db)
    _note(progress, f"  marked {warm} people as first-degree warm")
    return {"fiat_edges": fiat, "podcast_edges": podcasts, "warm_people": warm}
