"""
extraction_engine.py
--------------------

Very simple extraction helper for the capstone review UI.

For this phase, the goal is NOT to be clinically complete, but to:
- Take the OCR text file produced by `ocr_engine.py`.
- Break it into candidate "data points" that a human can review.
- Attach placeholder coding (FHIR / LOINC / etc.) that can be refined later.

This keeps the implementation lightweight, local, and easy to explain in
your capstone report.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, asdict
from typing import List

from clinical_lexicon import guess_category_from_keywords
from nlp_local import extract_candidates_from_text
from text_cleaning import clean_clinical_ocr_text

PROJECT_ROOT = pathlib.Path(".").resolve()


def _items_from_ocr_file(doc_stem: str) -> List[ExtractedItem]:
    """Rule-based + local NLP candidates from ``temp_extractions`` (no LLM)."""
    ocr_text_path = PROJECT_ROOT / "temp_extractions" / f"{doc_stem}.txt"
    if not ocr_text_path.exists():
        raise FileNotFoundError(f"OCR text file not found: {ocr_text_path}")

    raw = ocr_text_path.read_text(encoding="utf-8")
    body = clean_clinical_ocr_text(raw)
    items: List[ExtractedItem] = []
    candidates = extract_candidates_from_text(body)
    for idx, (cleaned, category, _conf, nlp_tags) in enumerate(candidates, start=1):
        item = ExtractedItem(
            id=str(idx),
            raw_text=cleaned,
            category=category,
            code_system="",
            code="",
            fhir_resource_type="Observation" if category == "lab_result" else "",
            nlp_tags=nlp_tags,
        )
        items.append(item)

    if not items:
        for idx, line in enumerate(body.splitlines(), start=1):
            cleaned = line.strip()
            if not cleaned:
                continue
            category = guess_category_from_keywords(cleaned.lower())
            if category == "other":
                continue
            items.append(
                ExtractedItem(
                    id=str(idx),
                    raw_text=cleaned,
                    category=category,
                    code_system="",
                    code="",
                    fhir_resource_type="Observation" if category == "lab_result" else "",
                )
            )

    return items


@dataclass
class ExtractedItem:
    """
    Minimal representation of an extracted data point for review.

    Attributes
    ----------
    id : str
        Stable identifier within a document (e.g., "1", "2", ...).
    raw_text : str
        The original line or snippet from the OCR text.
    category : str
        Simple label such as "lab_result", "medication", "problem", or "other".
    code_system : str
        Placeholder for coding system (e.g., "LOINC", "SNOMED", "RXNORM").
    code : str
        Placeholder for specific code. You can update these as your mapping
        logic becomes more sophisticated.
    fhir_resource_type : str
        Type of FHIR resource this line most closely maps to (e.g., "Observation").
    entity_role : str
        Optional hint from the LLM, e.g. patient vs ordering_provider vs lab_facility.
    nlp_tags : str
        When rule-based + spaCy path: short \"text:ENTITY\" list from local NLP.
    """

    id: str
    raw_text: str
    category: str = "other"
    code_system: str = ""
    code: str = ""
    fhir_resource_type: str = ""
    entity_role: str = ""
    nlp_tags: str = ""


def load_extracted_items(doc_stem: str) -> List[ExtractedItem]:
    """
    Load candidate extracted items for a given document.

    Preference order:
    1. If an LLM-derived JSON file exists in `review_data/<doc_stem>_llm.json`,
       load items from there **when non-empty**.
    2. If the LLM file exists but ``items`` is empty (or only blank rows), use the
       same rule-based / NLP scan as when no LLM file is present — so review is not
       stuck blank when the model skips structured items.
    3. Otherwise run that scan directly from OCR text.
    """
    # 1) Try LLM-derived JSON first (v2 object with demographics + items, or legacy list)
    llm_json_path = PROJECT_ROOT / "review_data" / f"{doc_stem}_llm.json"
    if llm_json_path.exists():
        with llm_json_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            raw_items = payload.get("items", [])
            if not isinstance(raw_items, list):
                raw_items = []
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []
        items: List[ExtractedItem] = []
        for idx, entry in enumerate(raw_items, start=1):
            if not isinstance(entry, dict):
                continue
            item = ExtractedItem(
                id=str(entry.get("id", idx)),
                raw_text=str(entry.get("raw_text", "")),
                category=str(entry.get("category", "other")),
                code_system=str(entry.get("code_system", "")),
                code=str(entry.get("code", "")),
                fhir_resource_type=str(entry.get("fhir_resource_type", "")),
                entity_role=str(entry.get("entity_role", "")),
                nlp_tags=str(entry.get("nlp_tags", "")),
            )
            items.append(item)
        meaningful = [i for i in items if (i.raw_text or "").strip()]
        if meaningful:
            return items

    return _items_from_ocr_file(doc_stem)


def save_confirmations(doc_stem: str, confirmations: dict) -> pathlib.Path:
    """
    Persist user confirmations to a JSON file on disk.

    Parameters
    ----------
    doc_stem : str
        Base document name (e.g. "Labs").
    confirmations : dict
        Mapping of item_id -> bool indicating whether the user confirmed
        that the interpreted data is correct.

    Returns
    -------
    pathlib.Path
        Path to the JSON file that stores the confirmation data.
    """
    review_dir = PROJECT_ROOT / "review_logs"
    review_dir.mkdir(parents=True, exist_ok=True)

    output_path = review_dir / f"{doc_stem}_confirmations.json"
    payload = {
        "document": doc_stem,
        "confirmations": confirmations,
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return output_path


def items_to_json_serialisable(items: List[ExtractedItem]) -> list:
    """
    Convert a list of ExtractedItem objects into a JSON-serialisable structure.
    """
    return [asdict(item) for item in items]


if __name__ == "__main__":  # pragma: no cover - simple manual test
    # Example: load items for "Labs" and print the first few.
    loaded_items = load_extracted_items("Labs")
    for item in loaded_items[:10]:
        print(item)

