"""ORM models for the VC warm-intro graph.

Enum-like columns are TEXT for SQLite friendliness; allowed values are the
constant tuples below, validated in the taxonomy / builder layers.

The load-bearing invariant lives on RelationshipEdge.structural: an edge is
persisted ONLY when a source structurally asserts the tie (a roster lists both
people, a funding announcement names investor+company, a Wikidata claim, a
podcast guest entry, a CSV row). Sentence co-occurrence never creates an edge.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- controlled vocabularies ----------------------------------------------
ORG_TYPES = ("firm", "company", "nonprofit", "school", "government", "event",
             "unknown")


class Person(Base):
    __tablename__ = "people"

    id = Column(String, primary_key=True, default=_uuid)
    canonical_name = Column(String, nullable=False)
    norm_name = Column(String, index=True, unique=True, nullable=False)
    # Authoritative identity anchor. Two notable people sharing a name have
    # distinct QIDs, so they never merge into one false-bridge node.
    wikidata_qid = Column(String, index=True, nullable=True)
    # How many language Wikipedia pages this QID has — a fame MAGNITUDE, not
    # just the binary fact of having a QID at all. Fetched once when the QID is
    # first adopted (see enrich._store_wikidata_identity). 0 means "not yet
    # measured" (e.g. a QID adopted before this column existed), which
    # graph.connect.fame_penalty treats as failing toward caution, not toward
    # permissiveness.
    wikidata_sitelinks = Column(Integer, default=0, nullable=False)
    # True when the person is in Drew's real first degree (podcast guest, Fiat
    # colleague, portfolio founder, LinkedIn connection).
    is_warm = Column(Boolean, default=False, nullable=False)
    # 1 once this person's own structured sources have been pulled, so a later
    # run reuses persisted neighbors instead of re-fetching.
    enriched = Column(Integer, default=0, nullable=False)
    aliases = Column(JSON, default=list)
    meta = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: _now().isoformat())


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    norm_name = Column(String, index=True, unique=True, nullable=False)
    type = Column(String, default="unknown")  # one of ORG_TYPES
    # Roster size as observed at ingest. Drives the Rule 1 mega-hub cap: an org
    # above config.MAX_ORG_MEMBERS_FOR_EDGES never materializes pairwise edges.
    member_count = Column(Integer, default=0, nullable=False)
    meta = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: _now().isoformat())


class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, default=_uuid)
    url = Column(String, index=True)
    title = Column(String)
    provider = Column(String)
    query_used = Column(String)
    fetched_at = Column(String, default=lambda: _now().isoformat())


class RelationshipEdge(Base):
    """Undirected person-person tie (or a person-org membership record).

    Stored with sorted (person_a_id, person_b_id) so an undirected pair has one
    canonical row. `cost` is the pathfinding weight, derived from warmth_tier.
    """

    __tablename__ = "relationship_edges"

    id = Column(String, primary_key=True, default=_uuid)
    person_a_id = Column(String, ForeignKey("people.id"), nullable=False, index=True)
    person_b_id = Column(String, ForeignKey("people.id"), nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=True, index=True)
    relationship_type = Column(String, nullable=False)
    warmth_tier = Column(Integer, default=5, nullable=False)   # 1 (warmest) .. 5
    cost = Column(Float, default=7.0, nullable=False)          # lower = warmer
    evidence_snippet = Column(Text)
    source_id = Column(String, ForeignKey("sources.id"), nullable=True)
    # Rule 0: MUST be True to persist. Guarded in builder.add_edge.
    structural = Column(Boolean, default=False, nullable=False)
    # Informational only (e.g. an LLM-derived implied_type/confidence hint
    # on a co_mention edge). Never read by Rule 0 or pathfinding's type checks.
    meta = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: _now().isoformat())

    person_a = relationship("Person", foreign_keys=[person_a_id])
    person_b = relationship("Person", foreign_keys=[person_b_id])
    organization = relationship("Organization", foreign_keys=[organization_id])
    source = relationship("Source", foreign_keys=[source_id])


class LocalProfile(Base):
    """One row of an uploaded LinkedIn CSV (optional booster layer)."""

    __tablename__ = "local_profiles"

    id = Column(String, primary_key=True, default=_uuid)
    canonical_name = Column(String, nullable=False)
    norm_name = Column(String, index=True)
    aliases = Column(JSON, default=list)
    email = Column(String, nullable=True, index=True)
    linkedin_url = Column(String, nullable=True)
    companies = Column(JSON, default=list)
    titles = Column(JSON, default=list)
    schools = Column(JSON, default=list)
    raw_row = Column(JSON, default=dict)
    created_at = Column(String, default=lambda: _now().isoformat())
