"""Convert raw HTML into clean visible text."""
from __future__ import annotations

import json
from typing import List

from bs4 import BeautifulSoup

from .. import config

_STRIP_TAGS = ["script", "style", "noscript", "head", "nav", "footer",
               "svg", "header", "aside", "form", "button", "template"]


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = soup_of(html)
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[: config.MAX_PAGE_CHARS]


def _walk_jsonld(node, want_type: str, out: list) -> None:
    """Collect the `name` of every object whose @type includes `want_type`."""
    if isinstance(node, dict):
        types = node.get("@type", "")
        types = types if isinstance(types, list) else [types]
        if want_type in types and isinstance(node.get("name"), str):
            out.append(node["name"].strip())
        for value in node.values():
            _walk_jsonld(value, want_type, out)
    elif isinstance(node, list):
        for item in node:
            _walk_jsonld(item, want_type, out)


def jsonld_names(html: str, schema_type: str = "Person") -> List[str]:
    """Names from schema.org `<script type="application/ld+json">` blocks.

    Modern site builders embed structured Person/Organization data — the
    cleanest possible roster source, and a first-class structural assertion: the
    page DECLARES "this person is on our team". It also survives JS rendering
    intact, where visible-text scraping fails (Bonfire's team names live only in
    a JSON-LD graph, never in a text node).
    """
    if not html or "ld+json" not in html:
        return []
    soup = soup_of(html)
    names: List[str] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        _walk_jsonld(data, schema_type, names)
    # dedupe, preserve order
    seen, out = set(), []
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _names_under(node, out: list) -> None:
    """Collect the `name` of every Person (or bare-name string) reachable from
    `node` — used on the value of a role property like `performer`/`organizer`,
    where every Person found IS in that role."""
    if isinstance(node, dict):
        types = node.get("@type", "")
        types = types if isinstance(types, list) else [types]
        # A role value is usually a Person, but some pages omit @type on the
        # object and just give a name. Accept Person, or an untyped {name:...}.
        if (("Person" in types or not any(types)) and isinstance(node.get("name"), str)):
            out.append(node["name"].strip())
    elif isinstance(node, list):
        for item in node:
            _names_under(item, out)
    elif isinstance(node, str):
        out.append(node.strip())


def _walk_events(node, out: list) -> None:
    """Find every schema.org Event and pull its speaker/organizer roles.

    An event's page structurally asserts two different things, and they must not
    be flattened together the way `jsonld_names(html, "Person")` would:
      * `performer` (also `performers`, `actor`) — the people who SPOKE.
      * `organizer` — the person/org that RAN it (the pivot that engaged every
        speaker even when they spoke at different times).
    A Person sitting elsewhere in the graph (an author byline, a testimonial)
    is neither, and is deliberately ignored.
    """
    if isinstance(node, dict):
        types = node.get("@type", "")
        types = types if isinstance(types, list) else [types]
        is_event = any(isinstance(t, str) and t.endswith("Event") for t in types)
        if is_event:
            speakers: list = []
            for key in ("performer", "performers", "actor"):
                if key in node:
                    _names_under(node[key], speakers)
            organizers: list = []
            if "organizer" in node:
                _names_under(node["organizer"], organizers)
            out.append({
                "name": (node.get("name") or "").strip() if isinstance(node.get("name"), str) else "",
                "url": (node.get("url") or "").strip() if isinstance(node.get("url"), str) else "",
                "start": (node.get("startDate") or "").strip() if isinstance(node.get("startDate"), str) else "",
                "speakers": speakers,
                "organizers": organizers,
            })
        for value in node.values():
            _walk_events(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_events(item, out)


def event_roles(html: str) -> List[dict]:
    """Every schema.org Event on the page, as
    [{name, url, start, speakers[], organizers[]}].

    A first-class structural assertion: the event DECLARES who performed and who
    organized it, in machine-readable JSON-LD that survives JS rendering. Empty
    when the page has no Event markup — the caller then has nothing to assert and
    must yield no edges (Rule 0)."""
    if not html or "ld+json" not in html:
        return []
    soup = soup_of(html)
    events: List[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        _walk_events(data, events)
    return events


def text_blocks(html: str, max_chars: int = 80) -> List[str]:
    """Visible text of each DOM element, as SEPARATE strings.

    Roster pages put a person's name in its own element. Flattening the page to
    one string glues neighbouring elements together, so Storm Ventures'
    `<div>Email</div><div>Ryan Floyd</div>` reads as "Email Ryan Floyd" and NER
    invents people ("Email Hoefler", "Floyd Ryan Floyd"). Keeping the boundaries
    is what makes a roster scrape trustworthy.

    Blocks longer than `max_chars` are prose, not roster cells, and are dropped.
    """
    if not html:
        return []
    soup = soup_of(html)
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    seen, blocks = set(), []
    for line in soup.get_text("\n", strip=True).splitlines():
        line = " ".join(line.split())
        if not line or len(line) > max_chars or line in seen:
            continue
        seen.add(line)
        blocks.append(line)
    return blocks
