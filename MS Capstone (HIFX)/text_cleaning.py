"""
text_cleaning.py
----------------

Normalize and denoise OCR output before NLP-style extraction (LLM, regex
demographics, rule-based review lines) and FHIR bundle creation.

Uses only the Python standard library (unicode normalization, regex). Set
``SKIP_OCR_TEXT_CLEAN=1`` to return text unchanged.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Final

# Characters to strip (format / zero-width); keep letters, marks (accents), emoji.
_DISALLOWED_CATEGORIES: Final[frozenset[str]] = frozenset({"Cf"})


def ocr_text_cleaning_enabled() -> bool:
    return os.environ.get("SKIP_OCR_TEXT_CLEAN", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    )


def clean_clinical_ocr_text(text: str) -> str:
    """
    Clean scanned-document OCR text for downstream extraction.

    Steps (idempotent enough for safe double application):
    - Unicode NFKC normalization
    - Remove most non-spacing marks / zero-width / format noise
    - Unify quotes, dashes, non-breaking spaces
    - Normalize newlines; trim each line and collapse internal runs of spaces
    - Collapse long blank runs between lines (keep at most one empty line)
    - Drop stray pipe characters often seen at line ends in OCR
    """
    if not ocr_text_cleaning_enabled():
        return text
    if not text:
        return ""

    s = unicodedata.normalize("NFKC", text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    out_chars: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in _DISALLOWED_CATEGORIES:
            continue
        # Keep newlines/tabs; drop other control characters (rare stray bytes from OCR).
        if cat == "Cc" and ch not in "\n\t":
            continue
        out_chars.append(ch)
    s = "".join(out_chars)

    _repl = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\ufeff": "",
    }
    for a, b in _repl.items():
        s = s.replace(a, b)

    lines: list[str] = []
    for line in s.split("\n"):
        line = line.strip()
        line = re.sub(r"\s*\|\s*$", "", line)
        line = re.sub(r"[ \t]+", " ", line)
        lines.append(line)

    # Collapse multiple blank lines to a single blank line between blocks.
    merged: list[str] = []
    prev_blank = False
    for ln in lines:
        is_blank = not ln
        if is_blank:
            if not prev_blank:
                merged.append("")
            prev_blank = True
        else:
            prev_blank = False
            merged.append(ln)

    while merged and not merged[-1]:
        merged.pop()
    while merged and not merged[0]:
        merged.pop(0)

    result = "\n".join(merged)
    return result.strip()
