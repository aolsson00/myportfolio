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
    """

    id: str
    raw_text: str
    category: str = "other"
    code_system: str = ""
    code: str = ""
    fhir_resource_type: str = ""


def _guess_category(line: str) -> str:
    """
    Very simple heuristic to label a line.

    This is intentionally naive for now; you can replace it with more
    advanced NLP or rule-based logic later.
    """
    lower = line.lower()

    # Demographics: patient name, DOB, MRN, sex, age
    if any(
        keyword in lower
        for keyword in [
            "dob",
            "date of birth",
            "birth date",
            "mrn",
            "medical record",
            "patient name",
            "name:",
            "sex:",
            "male",
            "female",
            "age:",
        ]
    ):
        return "demographic"

    # Lab results
    if any(
        keyword in lower
        for keyword in [
            "lab",
            "result",
            "hgb",
            "hemoglobin",
            "glucose",
            "a1c",
            "na ",
            "k ",
            "creatinine",
        ]
    ):
        return "lab_result"

    # Medications
    if any(
        keyword in lower
        for keyword in [
            "medication",
            "medications",
            "rx",
            "tablet",
            "capsule",
            "mg ",
            "mcg",
            "po ",
            "bid",
            "tid",
            "qhs",
        ]
    ):
        return "medication"

    # Problem list / diagnoses
    if any(
        keyword in lower
        for keyword in [
            "problem list",
            "diagnosis",
            "diagnoses",
            "dx:",
            "assessment",
            "icd",
        ]
    ):
        return "problem"

    # Orders (labs or imaging ordered)
    if any(
        keyword in lower
        for keyword in [
            "order:",
            "ordered",
            "test requested",
            "lab order",
            "imaging order",
        ]
    ):
        return "order"

    return "other"


def load_extracted_items(doc_stem: str) -> List[ExtractedItem]:
    """
    Load candidate extracted items for a given document.

    Preference order:
    1. If an LLM-derived JSON file exists in `review_data/<doc_stem>_llm.json`,
       load items from there.
    2. Otherwise, fall back to simple rule-based extraction over the OCR text.
    """
    project_root = pathlib.Path(".").resolve()

    # 1) Try LLM-derived JSON first
    llm_json_path = project_root / "review_data" / f"{doc_stem}_llm.json"
    if llm_json_path.exists():
        with llm_json_path.open("r", encoding="utf-8") as f:
            raw_items = json.load(f)
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
            )
            items.append(item)
        return items

    # 2) Fallback: rule-based extraction from OCR text
    ocr_text_path = project_root / "temp_extractions" / f"{doc_stem}.txt"
    if not ocr_text_path.exists():
        raise FileNotFoundError(f"OCR text file not found: {ocr_text_path}")

    items: List[ExtractedItem] = []
    with ocr_text_path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            cleaned = line.strip()
            if not cleaned:
                continue
            category = _guess_category(cleaned)
            # Only keep lines that look clinically relevant
            if category == "other":
                continue
            item = ExtractedItem(
                id=str(idx),
                raw_text=cleaned,
                category=category,
                code_system="",
                code="",
                fhir_resource_type="Observation" if category == "lab_result" else "",
            )
            items.append(item)

    return items


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
    project_root = pathlib.Path(".").resolve()
    review_dir = project_root / "review_logs"
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

