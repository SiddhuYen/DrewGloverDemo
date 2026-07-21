"""Optional .vcf (vCard) address-book import — a booster layer, like the CSV.

Drew exports his iPhone contacts (Contacts app → export, or iCloud → Export
vCard) and every entry becomes a tier-1 `address_book` edge to him: saving
someone in your phone is a structural assertion that you know them (Rule 0),
exactly as a LinkedIn CSV row is.

A contact usually carries a name. When it does not — a bare number saved with no
name — we resolve the number to its owner via Trestle's Reverse Phone API and
use that name. If Trestle is unconfigured or finds nothing, the contact is still
kept as an "Unknown (<number>)" placeholder so the first-degree link is never
silently dropped (it is a leaf: connected only to Drew, so it can bridge
nothing).

Handles the real quirks of exports: multiple VCARD blocks, RFC-6350 line folding
(continuation lines begin with a space/tab), Apple's grouped `item1.TEL`
properties, `tel:`-prefixed URI values, and the structured `N`/`ORG` fields.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..edges.names import name_variants, person_norm_key
from ..graph import builder
from ..models import LocalProfile
from ..providers import trestle

_WANTED = {"FN", "N", "TEL", "EMAIL", "ORG", "TITLE"}


def _unfold(text: str) -> List[str]:
    """RFC-6350 line unfolding: a line starting with a space or tab continues
    the previous logical line (this is how a long base64 PHOTO, or any wrapped
    value, is split across physical lines)."""
    out: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _split_structured(value: str) -> List[str]:
    """Split a structured value ("Family;Given;...") on UNescaped semicolons."""
    parts, buf, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            buf.append(value[i:i + 2])
            i += 2
            continue
        if ch == ";":
            parts.append(_unescape("".join(buf)))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append(_unescape("".join(buf)))
    return parts


def _parse_line(line: str):
    """-> (PROP, params, value) or None. Strips a grouping prefix like
    'item1.TEL' down to 'TEL'."""
    if ":" not in line:
        return None
    head, _, value = line.partition(":")
    segments = head.split(";")
    prop = segments[0]
    if "." in prop:                     # drop Apple's 'item1.' group prefix
        prop = prop.rsplit(".", 1)[1]
    prop = prop.strip().upper()
    params: Dict[str, List[str]] = {}
    for seg in segments[1:]:
        k, _, v = seg.partition("=")
        params.setdefault(k.strip().upper(), []).extend(
            p.strip() for p in v.split(",") if p.strip())
    return prop, params, value


def _clean_tel(value: str) -> str:
    v = value.strip()
    if v.lower().startswith("tel:"):
        v = v[4:]
    return v.split(";")[0].strip()      # drop any ;ext= / ;phone-context= tail


def _looks_like_phone(name: str) -> bool:
    """True when a 'name' is really just a phone number (some exports set FN to
    the number for an un-named contact)."""
    digits = [c for c in name if c.isdigit()]
    return len(digits) >= 7 and all(not c.isalpha() for c in name)


def _parse_cards(text: str) -> List[dict]:
    """Parse every VCARD block into {name, emails, phones, companies, titles}."""
    cards: List[dict] = []
    cur: Optional[dict] = None
    for line in _unfold(text):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VCARD":
            cur = {"fn": "", "n": "", "emails": [], "phones": [],
                   "companies": [], "titles": []}
            continue
        if stripped.upper() == "END:VCARD":
            if cur is not None:
                cards.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        parsed = _parse_line(line)
        if parsed is None:
            continue
        prop, _params, value = parsed
        if prop not in _WANTED:
            continue
        if prop == "FN":
            cur["fn"] = _unescape(value.strip())
        elif prop == "N":
            fields = _split_structured(value)
            family = fields[0] if len(fields) > 0 else ""
            given = fields[1] if len(fields) > 1 else ""
            cur["n"] = " ".join(p for p in (given, family) if p).strip()
        elif prop == "TEL":
            tel = _clean_tel(value)
            if tel and tel not in cur["phones"]:
                cur["phones"].append(tel)
        elif prop == "EMAIL":
            email = value.strip()
            if email and email not in cur["emails"]:
                cur["emails"].append(email)
        elif prop == "ORG":
            org = _split_structured(value)[0].strip()
            if org and org not in cur["companies"]:
                cur["companies"].append(org)
        elif prop == "TITLE":
            title = _unescape(value.strip())
            if title and title not in cur["titles"]:
                cur["titles"].append(title)
    return cards


def _display_name(card: dict) -> str:
    """FN if it is a real name, else the structured N. A contact whose FN was set
    to its own phone number (Apple does this for un-named entries) still yields a
    name when N is present, and otherwise falls through to phone resolution."""
    for candidate in (card["fn"], card["n"]):
        name = (candidate or "").strip()
        if name and not _looks_like_phone(name):
            return name
    return ""


def _profile(name: str, card: dict, phones: List[str]) -> dict:
    return {
        "canonical_name": name,
        "norm_name": person_norm_key(name),
        "aliases": sorted(v for v in name_variants(name) if v != name),
        "email": (card["emails"][0] if card["emails"] else None),
        "linkedin_url": None,
        "phones": phones,
        "companies": list(card["companies"]),
        "titles": list(card["titles"]),
        "schools": [],
        "raw_row": {k: v for k, v in {
            "fn": card["fn"], "n": card["n"], "emails": card["emails"],
            "phones": phones, "org": card["companies"],
            "title": card["titles"]}.items() if v},
    }


def _merge_profile(existing: LocalProfile, parsed: dict) -> None:
    for field in ("companies", "titles", "schools", "aliases", "phones"):
        merged = sorted(set(getattr(existing, field) or []) | set(parsed[field]))
        setattr(existing, field, merged)
    if not existing.email and parsed["email"]:
        existing.email = parsed["email"]


def ingest_vcf(db: Session, content: str, owner_name: str = "") -> dict:
    """Parse + persist a .vcf, linking every contact to `owner_name` as tier-1.

    A nameless contact is resolved through Trestle's reverse-phone lookup; on a
    miss it is kept as an "Unknown (<number>)" placeholder. Returns
    {created, updated, edges, skipped, resolved_via_phone, unresolved, people},
    where `people` is the imported roster in file order for the enrichment
    picker (same contract as the CSV import).
    """
    owner_name = owner_name or config.DEMO_SEED_NAME
    cards = _parse_cards(content)
    if not cards:
        return {"created": 0, "updated": 0, "edges": 0, "skipped": 0,
                "resolved_via_phone": 0, "unresolved": 0, "people": [],
                "error": "no vCard entries found"}

    owner = builder.get_or_create_person(db, owner_name, is_warm=True)
    if owner is None:
        return {"created": 0, "updated": 0, "edges": 0, "skipped": 0,
                "resolved_via_phone": 0, "unresolved": 0, "people": [],
                "error": f"could not resolve owner {owner_name!r}"}

    source = builder.get_or_create_source(
        db, f"address-book://{owner.norm_name}",
        title=f"{owner.canonical_name} — phone contacts (vCard)",
        provider="vcard")

    created = updated = edges = skipped = resolved_via_phone = unresolved = 0
    people: List[dict] = []
    listed: set = set()

    for card in cards:
        phones = [trestle.normalize_number(p) for p in card["phones"]]
        phones = [p for p in phones if p]
        name = _display_name(card)
        resolution = "name"

        if not name:
            if not phones:
                skipped += 1          # no name and no number: nothing to anchor
                continue
            looked = trestle.reverse_phone(phones[0])
            if looked and not _looks_like_phone(looked):
                name = looked.strip()
                resolution = "phone"
                resolved_via_phone += 1
            else:
                name = f"Unknown ({phones[0]})"
                resolution = "unknown"
                unresolved += 1

        parsed = _profile(name, card, phones)
        if not parsed["norm_name"]:
            skipped += 1
            continue

        existing = db.execute(select(LocalProfile).where(
            LocalProfile.norm_name == parsed["norm_name"])).scalar_one_or_none()
        # Same name, different verified email => a different person (mirrors CSV).
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
        # A contact may appear twice (multiple cards, or a number that resolves
        # to an already-named contact). They dedupe onto one node, so count and
        # link them once — add_edge returns the existing row for a repeat.
        if person.id in listed:
            continue
        listed.add(person.id)
        company = parsed["companies"][0] if parsed["companies"] else ""
        people.append({
            "name": parsed["canonical_name"],
            "company": company,
            "title": parsed["titles"][0] if parsed["titles"] else "",
            "enriched": bool(person.enriched),
            "resolved": resolution,
        })
        note = {
            "name": f"A saved contact in {owner.canonical_name}'s phone.",
            "phone": (f"Saved in {owner.canonical_name}'s phone; the number was "
                      "matched to this name via reverse-phone lookup."),
            "unknown": (f"A number saved in {owner.canonical_name}'s phone with "
                        "no name; the owner could not be resolved."),
        }[resolution]
        edge = builder.add_edge(db, owner, person, "address_book",
                                source=source, evidence=note)
        if edge is not None:
            edges += 1

    db.commit()
    return {"created": created, "updated": updated, "edges": edges,
            "skipped": skipped, "resolved_via_phone": resolved_via_phone,
            "unresolved": unresolved, "people": people}
