"""
nlp_local.py
------------

Local NLP using **spaCy** (`en_core_web_sm`): sentence segmentation, named-entity
signals, and optional LLM context summaries. Runs fully offline after the model
is installed.

Setup::

    pip install spacy
    python -m spacy download en_core_web_sm

Disable at runtime: ``SKIP_SPACY=1``
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from clinical_lexicon import guess_category_from_keywords, keyword_score

LOGGER = logging.getLogger(__name__)

_MAX_CHARS = 1_000_000
_nlp: Any = None
_spacy_unavailable: bool = False

def spacy_enabled() -> bool:
    return os.environ.get("SKIP_SPACY", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_nlp() -> Any:
    """Lazy-load spaCy model; return None if unavailable."""
    global _nlp, _spacy_unavailable
    if _spacy_unavailable:
        return None
    if _nlp is not None:
        return _nlp
    if not spacy_enabled():
        _spacy_unavailable = True
        return None
    try:
        import spacy

        _nlp = spacy.load("en_core_web_sm")
        LOGGER.info("Loaded spaCy model en_core_web_sm for local NLP.")
        return _nlp
    except Exception as exc:  # pragma: no cover - environment specific
        LOGGER.warning(
            "spaCy NLP disabled (%s). Install: pip install spacy && "
            "python -m spacy download en_core_web_sm",
            exc,
        )
        _spacy_unavailable = True
        return None


def is_nlp_available() -> bool:
    return load_nlp() is not None


def _accumulate_entity_scores(sent: Any, scores: dict[str, float]) -> None:
    sl = sent.text.lower()
    for ent in sent.ents:
        label = ent.label_
        et = ent.text.lower()
        if label == "DATE":
            scores["demographic"] = scores.get("demographic", 0.0) + 2.5
            scores["order"] = scores.get("order", 0.0) + 0.4
        elif label == "TIME":
            scores["demographic"] = scores.get("demographic", 0.0) + 1.0
        elif label == "PERSON":
            if any(x in et for x in ("dr.", "dr ", "md", "physician")) or "doctor" in sl:
                scores["order"] = scores.get("order", 0.0) + 2.0
            elif any(x in sl for x in ("patient", "mrn", "dob", "name:", "subject")):
                scores["demographic"] = scores.get("demographic", 0.0) + 2.2
            else:
                scores["demographic"] = scores.get("demographic", 0.0) + 1.0
                scores["order"] = scores.get("order", 0.0) + 0.8
        elif label in ("ORG", "FAC"):
            scores["order"] = scores.get("order", 0.0) + 1.4
            scores["lab_result"] = scores.get("lab_result", 0.0) + 0.9
        elif label in ("GPE", "LOC"):
            scores["demographic"] = scores.get("demographic", 0.0) + 1.6
        elif label in ("QUANTITY", "CARDINAL", "PERCENT"):
            scores["lab_result"] = scores.get("lab_result", 0.0) + 1.8
            scores["medication"] = scores.get("medication", 0.0) + 1.0


def classify_sentence(
    sent: Any,
) -> tuple[str, float, str]:
    """
    Combine lexicon scores with spaCy entities for one sentence span.

    Returns
    -------
    category : str
    confidence : float in [0, 1]
    nlp_tags : short entity summary for the review UI
    """
    text = sent.text.strip()
    lower = text.lower()
    scores = keyword_score(lower)
    _accumulate_entity_scores(sent, scores)

    tags = ",".join(f"{e.text}:{e.label_}" for e in sent.ents[:8])

    if not scores:
        cat = guess_category_from_keywords(lower)
        return (cat, 0.25 if cat != "other" else 0.0, tags)

    best = max(scores, key=scores.get)
    raw = scores[best]
    confidence = max(0.0, min(1.0, raw / 6.0))
    # Keyword-only lines: still allow if lexicon hit strong
    kw = guess_category_from_keywords(lower)
    if kw != "other" and kw != best and scores.get(kw, 0) >= 1.0:
        if scores[kw] + 0.5 >= raw:
            best = kw
            raw = scores[kw]
            confidence = max(0.0, min(1.0, raw / 6.0))
    return best, confidence, tags


def extract_candidates_from_text(body: str) -> list[tuple[str, str, float, str]]:
    """
    Segment with spaCy and return (text, category, confidence, nlp_tags).
    Falls back to line-based + keywords only if spaCy is off.
    """
    nlp = load_nlp()
    out: list[tuple[str, str, float, str]] = []

    if nlp is None:
        for line in body.splitlines():
            cleaned = line.strip()
            if len(cleaned) < 2:
                continue
            lower = cleaned.lower()
            cat = guess_category_from_keywords(lower)
            if cat == "other":
                continue
            out.append((cleaned, cat, 0.35, ""))
        return out

    doc = nlp(body[:_MAX_CHARS])
    for sent in doc.sents:
        st = sent.text.strip()
        if len(st) < 3:
            continue
        cat, conf, tags = classify_sentence(sent)
        if cat == "other" and conf < 0.25:
            continue
        if cat == "other":
            continue
        out.append((st, cat, conf, tags))

    return out


def build_llm_nlp_summary(text: str, max_chars: int = 4000) -> str:
    """
    Compact entity + sentence stats to prepend to LLM prompts (helps disambiguation).
    """
    if not spacy_enabled():
        return ""
    nlp = load_nlp()
    if nlp is None:
        return ""
    sample = text[:max_chars]
    doc = nlp(sample)
    labels = Counter(e.label_ for e in doc.ents)
    top = labels.most_common(12)
    parts = [f"{lb}:{n}" for lb, n in top]
    n_sents = len(list(doc.sents))
    lines = [
        "[NLP summary — spaCy en_core_web_sm]",
        f"Sentences in sample: {n_sents}; entities (top labels): " + "; ".join(parts),
    ]
    # Short list of unique entity texts (helps names/locations)
    seen: set[str] = set()
    names: list[str] = []
    for e in doc.ents:
        if e.label_ in ("PERSON", "ORG", "GPE", "FAC") and e.text.strip():
            key = (e.text.strip(), e.label_)
            if key in seen:
                continue
            seen.add(key)
            names.append(f"{e.text.strip()} ({e.label_})")
            if len(names) >= 15:
                break
    if names:
        lines.append("Key entities: " + "; ".join(names))
    return "\n".join(lines) + "\n\n"


def nlp_healthcheck() -> dict[str, Any]:
    """For debugging / README: report whether local NLP is usable."""
    ok = is_nlp_available()
    return {
        "spacy_requested": spacy_enabled(),
        "nlp_loaded": ok,
        "model": "en_core_web_sm" if ok else None,
    }
