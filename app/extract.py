"""spaCy NER — the only entity extractor.

NER-only (`en_core_web_sm`, parser/lemmatizer disabled) for speed. Its output is
always passed through the deterministic name-shape filters in edges/names.py.

No LLM is used anywhere in extraction. An LLM entity filter was tried and
rejected: it deleted real co-founders while keeping page-title artifacts like
"Drew Glover - LinkedIn". Name shape is a syntactic property, so a syntactic
filter is cheaper and strictly more reliable.
"""
from __future__ import annotations

import threading
from typing import List

_nlp = None
_lock = threading.Lock()


def _model():
    """Load `en_core_web_sm` once, lazily. Returns None when unavailable, so the
    rest of the system degrades to structured providers rather than crashing."""
    global _nlp
    if _nlp is None:
        with _lock:
            if _nlp is None:
                try:
                    import spacy
                    # Keep the tagger + attribute_ruler: part-of-speech is what
                    # separates "Sheel Mohnot" (PROPN PROPN) from "Reserve Your
                    # Spot" (VERB PRON NOUN). NER alone cannot — it labels
                    # neither. Drop only the parser and lemmatizer.
                    _nlp = spacy.load("en_core_web_sm",
                                      disable=["lemmatizer", "parser"])
                except Exception:
                    _nlp = False  # sentinel: tried and failed
    return _nlp or None


def available() -> bool:
    return _model() is not None


def person_names(text: str) -> List[str]:
    """Raw PERSON spans from `text`, in document order (unfiltered, undeduped).

    Callers MUST pass the result through builder.clean_person_names.
    """
    nlp = _model()
    if not nlp or not text:
        return []
    return [ent.text for ent in nlp(text).ents if ent.label_ == "PERSON"]


def org_names(text: str) -> List[str]:
    nlp = _model()
    if not nlp or not text:
        return []
    return [ent.text for ent in nlp(text).ents if ent.label_ == "ORG"]


# Entity labels that positively identify a candidate as NOT a person.
_NON_PERSON_LABELS = {"ORG", "GPE", "LOC", "FAC", "DATE", "TIME", "MONEY",
                      "PERCENT", "CARDINAL", "ORDINAL", "PRODUCT", "EVENT",
                      "WORK_OF_ART", "LAW", "LANGUAGE", "NORP", "QUANTITY"}

# Nobiliary/patronymic particles carry a non-PROPN tag ("de" -> X) but are part
# of the surname: "JC Bahr-de Stefano", "Ludwig van Beethoven".
_PARTICLES = {"de", "del", "della", "der", "den", "di", "da", "du", "van",
              "von", "la", "le", "bin", "ibn", "al", "ter", "ten", "af", "of"}


def filter_person_blocks(candidates: List[str]) -> List[str]:
    """Keep only the roster candidates that are plausibly personal names.

    Two grammatical signals, because neither suffices alone:

      * POS (accept):  every token must be a proper noun (or a surname
        particle). This is what separates "Sheel Mohnot" (PROPN PROPN) from
        "Reserve Your Spot" (VERB PRON NOUN) and "Current EIR" (ADJ PROPN).
      * NER (reject):  drop what the model affirmatively types as a place or an
        organization, e.g. "Silicon Valley" (LOC) — all proper nouns, yet not a
        person.

    Requiring a PERSON entity, as the previous engine did, is the wrong test:
    `en_core_web_sm` tags neither "Sheel Mohnot" nor "Brainstorming Session",
    so it would delete a real co-founder while admitting page furniture.

    Degrades to a no-op (name-shape filtering only) when the model is absent.
    """
    nlp = _model()
    if not nlp or not candidates:
        return list(candidates)

    kept = []
    for text, doc in zip(candidates, nlp.pipe(candidates)):
        # A label spanning the WHOLE candidate is decisive; a stray tag on one
        # token of "Mary Anderson" must not condemn the name.
        if any(ent.label_ in _NON_PERSON_LABELS
               and ent.text.strip() == text.strip() for ent in doc.ents):
            continue
        tokens = [t for t in doc if not t.is_punct and not t.is_space]
        if not tokens:
            continue
        if all(t.pos_ == "PROPN" or t.text.lower() in _PARTICLES for t in tokens):
            kept.append(text)
    return kept
