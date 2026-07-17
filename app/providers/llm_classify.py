"""Claude-backed relationship-strength labeling for the co_mention tier ONLY.

spaCy co-occurrence mining (comention.py) tells us two names appeared on the
same deep-search page; it cannot tell us whether the surrounding prose reads
like "co-founded X together" or "photographed at the same gala." This asks
Claude to label that from the evidence snippet alone, via a structured
output (output_format=_LabelResult) so the model can't return malformed JSON.

This NEVER promotes an edge: relationship_type stays "co_mention" and Rule 0
(edges/taxonomy.py) is untouched. The output is stored as metadata only — an
`implied_type` + `confidence` hint for display and a bounded within-tier cost
nudge (see builder.add_edge). A confidently-labeled "cofounder-sounding"
co-mention is still capped well below the weakest real structural tie.

Uses config.CLAUDE_API_KEY — a real Anthropic key, spend-capped in the
Anthropic Console rather than routed through a proxy (see DESKTOP.md for
why: one trusted user, zero hosting infrastructure). Auto no-op (all
"unknown"/0.0) when no key is configured or a request fails, so the
pipeline degrades the same way it did without Ollama: metadata just
doesn't get added, nothing else changes.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

import anthropic
from pydantic import BaseModel

from .. import config
from ..edges import taxonomy
from . import cache

# Informational vocabulary only — never written to RelationshipEdge.relationship_type.
# Excludes co_mention/cooccurrence (not real labels) and org_membership (not a
# person-person tie), so the model can't reach for something outside the tier.
_ALLOWED = sorted(
    t for t in taxonomy.RELATIONSHIPS
    if t not in ("co_mention", "cooccurrence", "org_membership")
) + ["unknown"]

_PROMPT = """You label what kind of relationship a snippet of article text IMPLIES
between two people who were merely named in the same article. This is a hint
about the tone of the text, NOT a verified relationship.

Allowed labels: {allowed}.
Rules:
- Pick the single best label the snippet's wording actually suggests.
- Use "unknown" if the snippet doesn't clearly suggest any relationship.
- confidence is 0..1 (how clearly the wording supports the label).
- Return exactly one result per item below, in the same order.

Items:
{items}
"""


class _LabelItem(BaseModel):
    label: str
    confidence: float


class _LabelResult(BaseModel):
    results: List[_LabelItem]


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
    return _client


def llm_available() -> bool:
    return bool(config.CLAUDE_API_KEY)


def is_active() -> bool:
    return bool(config.LLM_CLASSIFY_ENABLED) and llm_available()


def _key(a: str, b: str, evidence: str) -> str:
    h = hashlib.sha1(f"{a}||{b}||{evidence}".encode("utf-8")).hexdigest()[:16]
    return cache.make_key("llmclassify", "v1", h)


def _ask(items: List[dict]) -> List[_LabelItem]:
    lines = []
    for i, it in enumerate(items, 1):
        ev = (it["evidence"] or "")[:240].replace("\n", " ")
        lines.append(f'{i}. A="{it["a"]}" B="{it["b"]}" text="{ev}"')
    prompt = _PROMPT.format(allowed=", ".join(_ALLOWED), items="\n".join(lines))
    try:
        response = _get_client().messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            output_format=_LabelResult,
        )
        return response.parsed_output.results
    except Exception:
        return []


def classify(items: List[dict]) -> List[dict]:
    """items: [{a, b, evidence}] -> [{label, confidence}] aligned by index.

    No-op ("unknown"/0.0 for everything) when inactive. Cached per (a, b,
    evidence) so re-running deep search on the same article doesn't re-ask.
    """
    results: List[dict] = [{"label": "unknown", "confidence": 0.0} for _ in items]
    if not items or not is_active():
        return results

    pending = []  # (orig_index, item)
    for idx, it in enumerate(items):
        if not it.get("evidence"):
            continue
        cached = cache.get(_key(it["a"], it["b"], it["evidence"]), track=False)
        if cached is not None:
            results[idx] = cached
        else:
            pending.append((idx, it))

    for start in range(0, len(pending), config.LLM_CLASSIFY_BATCH):
        chunk = pending[start:start + config.LLM_CLASSIFY_BATCH]
        answers = _ask([it for _idx, it in chunk])
        for n, (orig_idx, it) in enumerate(chunk):
            answer = answers[n] if n < len(answers) else None
            label = answer.label if answer else "unknown"
            if label not in _ALLOWED:
                label = "unknown"
            try:
                conf = float(answer.confidence) if answer else 0.0
            except (TypeError, ValueError):
                conf = 0.0
            out = {"label": label, "confidence": max(0.0, min(conf, 1.0))}
            results[orig_idx] = out
            cache.set(_key(it["a"], it["b"], it["evidence"]), "llmclassify", out,
                      config.CACHE_TTL)
    return results
