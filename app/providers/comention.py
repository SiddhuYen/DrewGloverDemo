"""Co-occurrence mining — the OPT-IN weak `co_mention` tier.

THIS IS NOT RULE 0. Two people merely NAMED TOGETHER on a page are not tied;
that co-occurrence is the exact failure Rule 0 exists to prevent (it produced
the bogus "Drew -> David Roos -> Jason Calacanis -> Sam Altman" path). This
silo exists only for the explicitly-toggled hybrid mode: it runs only when
config.CO_MENTION_ENABLED, its edges are traversed only when a query passes
include_weak=True, they are priced at tier 6 (punishing), and every one is
labelled "not a confirmed relationship".

For a subject, it web-searches them, reads the top pages, and returns every
OTHER person spaCy NER names on those pages, each with its source URL.
"""
from __future__ import annotations

from typing import Dict, List

from .. import config, extract
from ..edges.names import is_noise_name, person_norm_key, strip_role_affixes
from .base import fetch_page
from .htmltext import html_to_text


def _readable_text(url: str) -> str:
    """Plain-fetch a page's text, falling back to a headless render when the GET
    returns a JavaScript shell (a Forbes/LinkedIn profile is 400 KB of HTML with
    zero readable text). Degrades to the plain fetch when no browser is present.
    """
    page = fetch_page(url)
    if page.status_code != 200 or not page.content:
        return ""
    text = html_to_text(page.content)
    if text.strip():
        return text
    from .browser import available as _browser_available
    if _browser_available():
        rendered = fetch_page(url, render=True)
        if rendered.content:
            return html_to_text(rendered.content)
    return ""


class CoMentionProvider:
    name = "comention"

    def __init__(self, search_provider=None) -> None:
        self._search = search_provider

    def _available(self) -> bool:
        return self._search is not None and self._search.available()

    def co_mentions(self, name: str) -> List[Dict[str, str]]:
        """[{name, source_url}] — people co-mentioned with the subject."""
        if not name or not self._available() or not extract.available():
            return []
        subject_key = person_norm_key(name)
        urls: List[str] = []
        for query in (f'"{name}"',
                      f'"{name}" interview OR profile OR news OR announcement'):
            for result in self._search.search(query):
                if result.url not in urls:
                    urls.append(result.url)

        out: List[Dict[str, str]] = []
        seen = {subject_key}
        readable = 0
        for url in urls:
            if readable >= config.CO_MENTION_MAX_PAGES:
                break
            text = _readable_text(url)
            if not text:
                continue                 # JS shell / blocked — don't spend budget
            readable += 1
            for cand in extract.filter_person_blocks(extract.person_names(text)):
                cand = strip_role_affixes(cand).strip()
                if not cand or is_noise_name(cand):
                    continue
                key = person_norm_key(cand)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append({"name": cand, "source_url": url})
                if len(out) >= config.CO_MENTION_MAX_PER_PERSON:
                    return out
        return out
