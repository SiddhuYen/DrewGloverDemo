"""Ollama-backed relationship-strength labeling for the co_mention tier ONLY.

spaCy co-occurrence mining (comention.py) tells us two names appeared on the
same deep-search page; it cannot tell us whether the surrounding prose reads
like "co-founded X together" or "photographed at the same gala." This asks a
local Ollama model to label that from the evidence snippet alone.

This NEVER promotes an edge: relationship_type stays "co_mention" and Rule 0
(edges/taxonomy.py) is untouched. The output is stored as metadata only — an
`implied_type` + `confidence` hint for display and a bounded within-tier cost
nudge (see builder.add_edge). A confidently-labeled "cofounder-sounding"
co-mention is still capped well below the weakest real structural tie.

Deterministic (temperature 0) so the same evidence always yields the same
label and results cache cleanly by evidence hash — "non-deterministic" here
means "an LLM's judgment call," in contrast to the fixed provider-to-type
rules the rest of the graph uses, not sampling variance.

Auto no-op (all "unknown"/0.0) when the daemon is unreachable or disabled.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, List

import httpx

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

Return ONLY JSON mapping each item number to {{"label": "...", "confidence": 0.x}}:
{{"1": {{"label": "cofounder", "confidence": 0.7}}, ...}}

Items:
{items}
"""

_availability_cache: bool | None = None


def ollama_available() -> bool:
    global _availability_cache
    if _availability_cache is not None:
        return _availability_cache
    try:
        resp = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2.0)
        _availability_cache = resp.status_code == 200
    except Exception:
        _availability_cache = False
    return _availability_cache


def is_active() -> bool:
    return bool(config.OLLAMA_CLASSIFY_RELATIONS) and ollama_available()


def _key(a: str, b: str, evidence: str) -> str:
    h = hashlib.sha1(f"{a}||{b}||{evidence}".encode("utf-8")).hexdigest()[:16]
    return cache.make_key("ollamaclassify", "v1", h)


def _loose_json(raw: str):
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
    return {}


def _ask(items: List[dict]) -> Dict[str, dict]:
    lines = []
    for i, it in enumerate(items, 1):
        ev = (it["evidence"] or "")[:240].replace("\n", " ")
        lines.append(f'{i}. A="{it["a"]}" B="{it["b"]}" text="{ev}"')
    prompt = _PROMPT.format(allowed=", ".join(_ALLOWED), items="\n".join(lines))
    try:
        resp = httpx.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={"model": config.OLLAMA_MODEL, "prompt": prompt,
                  "stream": False, "format": "json", "options": {"temperature": 0.0}},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return {}
        raw = resp.json().get("response", "")
        data = json.loads(raw) if raw.strip().startswith("{") else _loose_json(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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

    for start in range(0, len(pending), config.OLLAMA_CLASSIFY_BATCH):
        chunk = pending[start:start + config.OLLAMA_CLASSIFY_BATCH]
        verdicts = _ask([it for _idx, it in chunk])
        for n, (orig_idx, it) in enumerate(chunk, 1):
            v = verdicts.get(str(n)) or {}
            label = v.get("label", "unknown")
            if label not in _ALLOWED:
                label = "unknown"
            try:
                conf = float(v.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            out = {"label": label, "confidence": max(0.0, min(conf, 1.0))}
            results[orig_idx] = out
            cache.set(_key(it["a"], it["b"], it["evidence"]), "ollamaclassify", out,
                      config.CACHE_TTL)
    return results
