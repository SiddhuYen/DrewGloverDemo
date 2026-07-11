"""Optional LinkedIn CSV import — a booster layer, never a dependency.

The demo works without it (Drew's first degree is seeded from public sources).
When he provides his export, every row becomes a tier-1 `linkedin_1st` edge to
him: a CSV row is a structural assertion that the connection exists.

Handles the real quirks of LinkedIn exports: a "Notes:" preamble before the
header, variant/missing column names, and multi-valued cells.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..edges.names import name_variants, person_norm_key
from ..graph import builder
from ..models import LocalProfile

# header alias -> canonical field (compared with case/space/underscore ignored)
_HEADER_ALIASES = {
    "firstname": "first_name", "first": "first_name",
    "lastname": "last_name", "last": "last_name", "surname": "last_name",
    "name": "name", "fullname": "name",
    "company": "company", "organization": "company", "organisation": "company",
    "currentcompany": "company", "employer": "company",
    "position": "title", "title": "title", "role": "title", "headline": "title",
    "email": "email", "emailaddress": "email", "emailaddresses": "email",
    "school": "school", "education": "school",
    "url": "url", "profileurl": "url", "linkedinurl": "url", "publicurl": "url",
}

_HEADER_HINTS = ("first name", "last name", "name,", "email", "company", "url")


def _canon_header(h: str) -> str:
    key = "".join(ch for ch in (h or "").lower() if ch.isalnum())
    return _HEADER_ALIASES.get(key, key)


def _get(row: Dict[str, str], field: str) -> str:
    val = row.get(field)
    return val.strip() if isinstance(val, str) else ""


def _as_list(value: str) -> List[str]:
    """Split a multi-valued cell on ';' or '|' — never ',', since names and org
    names legitimately contain commas."""
    if not value:
        return []
    return [p.strip() for p in value.replace("|", ";").split(";") if p.strip()]


def _strip_preamble(text: str) -> str:
    """LinkedIn prepends a 'Notes:' blurb before the real header row."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if (("first name" in low and "last name" in low)
                or low.startswith("name,") or ",name," in low
                or (sum(h in low for h in _HEADER_HINTS) >= 2 and "," in line)):
            return "\n".join(lines[i:])
    return text


def _profile_from_row(raw: Dict[str, str]) -> Optional[dict]:
    row = {_canon_header(k): (v or "") for k, v in raw.items()}
    name = _get(row, "name")
    if not name:
        name = " ".join(p for p in (_get(row, "first_name"),
                                    _get(row, "last_name")) if p).strip()
    if not name:
        return None  # a profile cannot be anchored without a name
    return {
        "canonical_name": name,
        "norm_name": person_norm_key(name),
        "aliases": sorted(v for v in name_variants(name) if v != name),
        "email": _get(row, "email") or None,
        "linkedin_url": _get(row, "url") or None,
        "companies": _as_list(_get(row, "company")),
        "titles": _as_list(_get(row, "title")),
        "schools": _as_list(_get(row, "school")),
        "raw_row": {k: v for k, v in raw.items() if v},
    }


def ingest_csv(db: Session, content: str, owner_name: str = "") -> dict:
    """Parse + persist a CSV, linking every row to `owner_name` as tier-1.

    Returns {created, updated, edges, skipped}.
    """
    owner_name = owner_name or config.DEMO_SEED_NAME
    text = _strip_preamble(content.lstrip("﻿"))
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {"created": 0, "updated": 0, "edges": 0, "skipped": 0,
                "error": "empty or headerless CSV"}

    owner = builder.get_or_create_person(db, owner_name, is_warm=True)
    if owner is None:
        return {"created": 0, "updated": 0, "edges": 0, "skipped": 0,
                "error": f"could not resolve owner {owner_name!r}"}

    source = builder.get_or_create_source(
        db, f"linkedin-csv://{owner.norm_name}",
        title=f"{owner.canonical_name} — LinkedIn connections export",
        provider="linkedin_csv")

    created = updated = edges = skipped = 0
    for raw in reader:
        parsed = _profile_from_row(raw)
        if parsed is None:
            skipped += 1
            continue

        existing = db.execute(select(LocalProfile).where(
            LocalProfile.norm_name == parsed["norm_name"])).scalar_one_or_none()
        # Same name, different verified email => a different person.
        if (existing and parsed["email"] and existing.email
                and existing.email.lower() != parsed["email"].lower()):
            existing = None

        if existing:
            _merge_profile(existing, parsed)
            updated += 1
        else:
            db.add(LocalProfile(**parsed))
            created += 1

        person = builder.get_or_create_person(db, parsed["canonical_name"],
                                              is_warm=True)
        if person is None or person.id == owner.id:
            continue
        company = parsed["companies"][0] if parsed["companies"] else ""
        edge = builder.add_edge(
            db, owner, person, "linkedin_1st", source=source,
            evidence=(f"A direct LinkedIn connection of {owner.canonical_name}"
                      f"{f' ({company})' if company else ''}."))
        if edge is not None:
            edges += 1

    db.commit()
    return {"created": created, "updated": updated, "edges": edges,
            "skipped": skipped}


def _merge_profile(existing: LocalProfile, parsed: dict) -> None:
    for field in ("companies", "titles", "schools", "aliases"):
        merged = sorted(set(getattr(existing, field) or []) | set(parsed[field]))
        setattr(existing, field, merged)
    for field in ("email", "linkedin_url"):
        if not getattr(existing, field) and parsed[field]:
            setattr(existing, field, parsed[field])
