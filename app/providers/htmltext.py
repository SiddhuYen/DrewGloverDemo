"""Convert raw HTML into clean visible text."""
from __future__ import annotations

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
