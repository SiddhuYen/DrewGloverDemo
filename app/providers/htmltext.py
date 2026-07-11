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
