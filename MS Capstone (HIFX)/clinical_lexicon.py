"""
clinical_lexicon.py
-------------------

Shared keyword sets and line classification for rule-based + NLP-assisted
extraction (single source of truth).
"""

from __future__ import annotations

import re

_KEYWORDS_DEMOGRAPHIC = frozenset({
    "dob", "date of birth", "birth date", "mrn", "medical record",
    "patient name", "name:", "sex:", "male", "female", "age:",
})
_KEYWORDS_LAB = frozenset({
    "lab", "laboratory", "result", "results", "hgb", "hemoglobin", "glucose", "a1c",
    "na ", "k ", "creatinine", "bun", "wbc", "rbc", "platelet",
    "reference range", "ref range", "units", "mg/dl", "mmol",
    "cbc", "cmp", "bmp", "phosphate", "bilirubin", "alt", "ast", "tsh",
    "vitals", "vital signs", "blood pressure", "heart rate", "temp ", "temperature",
})
_KEYWORDS_MED = frozenset({
    "medication", "medications", "rx", "tablet", "capsule",
    "mg ", "mcg", "po ", "bid", "tid", "qhs", "prescription",
    "sig:", "dispense", "refill", "pharmacy",
})
_KEYWORDS_PROBLEM = frozenset({
    "problem list", "diagnosis", "diagnoses", "dx:", "assessment", "icd",
    "history of", "pmh", "fam hx", "family history", "allerg", "immunization",
})
_KEYWORDS_ORDER = frozenset({
    "order:", "ordered", "test requested", "lab order", "imaging order",
})

# Short lab tokens that appear inside common English words (health**care**, he**alt**hcare).
_BOUNDARY_LAB_TOKENS = frozenset({
    "alt", "ast", "bun", "tsh", "cbc", "cmp", "bmp", "wbc", "rbc", "hgb",
})


def _keyword_matches(keyword: str, line_lower: str) -> bool:
    """
    Match a lexicon keyword without false positives (e.g. *lab* in *healthcare*).
    """
    if keyword == "lab":
        return bool(re.search(r"\blabs?\b", line_lower))
    if keyword == "rx":
        return bool(re.search(r"\brx\b", line_lower))
    if keyword in _BOUNDARY_LAB_TOKENS:
        return bool(re.search(rf"\b{re.escape(keyword)}\b", line_lower))
    return keyword in line_lower


def guess_category_from_keywords(line_lower: str) -> str:
    """Return clinical category from substring keywords, or \"other\"."""
    if any(_keyword_matches(k, line_lower) for k in _KEYWORDS_DEMOGRAPHIC):
        return "demographic"
    if any(_keyword_matches(k, line_lower) for k in _KEYWORDS_LAB):
        return "lab_result"
    if any(_keyword_matches(k, line_lower) for k in _KEYWORDS_MED):
        return "medication"
    if any(_keyword_matches(k, line_lower) for k in _KEYWORDS_PROBLEM):
        return "problem"
    if any(_keyword_matches(k, line_lower) for k in _KEYWORDS_ORDER):
        return "order"
    return "other"


def keyword_score(line_lower: str) -> dict[str, float]:
    """Soft scores per category from keyword hits (for combining with NLP)."""
    scores: dict[str, float] = {}
    for k in _KEYWORDS_DEMOGRAPHIC:
        if _keyword_matches(k, line_lower):
            scores["demographic"] = scores.get("demographic", 0.0) + 1.0
    for k in _KEYWORDS_LAB:
        if _keyword_matches(k, line_lower):
            scores["lab_result"] = scores.get("lab_result", 0.0) + 1.0
    for k in _KEYWORDS_MED:
        if _keyword_matches(k, line_lower):
            scores["medication"] = scores.get("medication", 0.0) + 1.0
    for k in _KEYWORDS_PROBLEM:
        if _keyword_matches(k, line_lower):
            scores["problem"] = scores.get("problem", 0.0) + 1.0
    for k in _KEYWORDS_ORDER:
        if _keyword_matches(k, line_lower):
            scores["order"] = scores.get("order", 0.0) + 1.0
    return scores
