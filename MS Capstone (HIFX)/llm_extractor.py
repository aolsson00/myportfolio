"""
llm_extractor.py
----------------

Helper that uses a local LLM (e.g., via Ollama) to turn OCR text into:
- Structured **demographics** (patient vs provider disambiguation)
- **Provider / lab roles** (ordering clinician, facility, referring provider)
- **Clinical line items** for the Review screen

By default this runs automatically after OCR via ocr_engine.extract_text_from_pdf.
Set SKIP_LLM=1 to disable.

Output: `review_data/<doc_stem>_llm.json` — schema v2 object with
``demographics``, ``providers_and_roles``, and ``items`` (legacy list-only files
still load for Review).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from typing import Any, List, Optional, Tuple

import requests


LOGGER = logging.getLogger(__name__)


BASE_DIR = pathlib.Path(".").resolve()
OCR_DIR = BASE_DIR / "temp_extractions"
REVIEW_DATA_DIR = BASE_DIR / "review_data"

# You can change these via environment variables if needed
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3")  # e.g. `llama3`, `mistral`, etc.
LLM_OCR_CHARS = int(os.environ.get("LLM_OCR_CHARS", "12000"))


def llm_extraction_enabled() -> bool:
    """
    LLM extraction runs after OCR by default. Set SKIP_LLM=1 to disable
    (e.g. when Ollama is not installed).
    """
    return os.environ.get("SKIP_LLM", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    )


def maybe_run_llm_extraction_after_ocr(doc_stem: str) -> Optional[bool]:
    """
    Run LLM extraction after OCR text exists. Safe to call on every document.

    Returns
    -------
    Optional[bool]
        ``None`` — skipped (SKIP_LLM or missing OCR file).
        ``True`` — wrote ``review_data/<doc_stem>_llm.json``.
        ``False`` — attempted but failed (Ollama down, timeout, parse error, etc.).
    """
    if not llm_extraction_enabled():
        LOGGER.info("LLM extraction skipped (SKIP_LLM is set).")
        return None

    ocr_path = OCR_DIR / f"{doc_stem}.txt"
    if not ocr_path.exists():
        LOGGER.warning("LLM extraction skipped: OCR file missing: %s", ocr_path)
        return None

    try:
        out_path = run_llm_extraction(doc_stem)
        LOGGER.info("LLM extraction saved: %s", out_path)
        return True
    except Exception as exc:
        LOGGER.warning(
            "LLM extraction failed for %s (review UI will use rule-based items): %s",
            doc_stem,
            exc,
        )
        return False


def _strip_json_fence(raw: str) -> str:
    raw = raw.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```\s*$", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return raw


def _parse_llm_document(raw_content: str) -> Tuple[List[dict[str, Any]], dict, dict]:
    """
    Parse LLM output into items list + demographics + providers dicts.
    Accepts v2 JSON object, or legacy JSON array of items only.
    """
    raw_content = _strip_json_fence(raw_content)

    def try_load(s: str) -> Any:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    parsed = try_load(raw_content)
    if parsed is None:
        # Brace slice (object)
        start = raw_content.find("{")
        end = raw_content.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = try_load(raw_content[start : end + 1])

    if parsed is None:
        # Legacy: array slice
        start = raw_content.find("[")
        end = raw_content.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = try_load(raw_content[start : end + 1])

    if isinstance(parsed, list):
        return parsed, {}, {}

    if isinstance(parsed, dict):
        items = parsed.get("items", [])
        if not isinstance(items, list):
            items = []
        demo = parsed.get("demographics") or {}
        prov = parsed.get("providers_and_roles") or {}
        if not isinstance(demo, dict):
            demo = {}
        if not isinstance(prov, dict):
            prov = {}
        return items, demo, prov

    return [], {}, {}


def _build_prompt(ocr_text: str) -> str:
    """
    Ask the model to separate patient vs providers vs lab context and emit strict JSON.
    """
    return (
        "You are a clinical document analyst for a health informatics system.\n"
        "You receive noisy OCR text from scanned PDFs (Tesseract + EasyOCR). "
        "Your job is to DISAMBIGUATE roles and extract structured facts.\n\n"
        "Rules:\n"
        "- **Patient** fields: only the person the record is **about**. "
        "Use labels like Patient, Name (next to DOB/MRN), Subject, or clearly "
        "demographic blocks. Do NOT put the ordering physician, signing doctor, "
        "lab director, or referral doctor into patient_name.\n"
        "- **Ordering provider** (labs/tests): who ordered the test — phrases like "
        "'Ordered by', 'Ordering provider', 'Physician', 'Signed by' on a requisition, "
        "or 'Referring' when it clearly means who ordered. If the name appears only "
        "as 'resulting pathologist' or 'lab director', put that under lab_facility "
        "notes or omit unless it is clearly the ordering clinician.\n"
        "- **Referring provider**: 'Referring physician', 'PCP', 'sent by' when distinct "
        "from ordering.\n"
        "- **Lab / facility**: facility name on letterhead, 'Performed at', 'Lab:', "
        "hospital name on a report — not the patient's home address.\n"
        "- **Address** (strict): only **street, city, state, ZIP** (postal address). "
        "Never append Age, Account Number, MRN, Phone, DOB, Sex, or other labels to "
        "the address string — put those in **age**, **mrn**, **phone**, etc. If OCR "
        "runs several fields on one line, split them into the correct JSON keys only.\n"
        "- **Phone**: digits and formatting only; no other labels in the same string.\n"
        "- **Address / phone** sourcing: prefer the **patient's** home/contact when labeled; "
        "if only a facility address is visible, leave patient address empty and "
        "mention facility under lab_facility.\n"
        "- **Age / DOB**: extract both when present; do not guess.\n"
        "- **Compound / two-part surnames** (e.g. Spanish maternal+paternal, hyphenated): "
        "put **all** surnames together in **family_name** (e.g. \"Garcia Lopez\" or "
        "\"Smith-Jones\"). Put **all** given names (first + middle) in **given_name**. "
        "Do **not** put a surname in given_name or a given name in family_name. "
        "**patient_name** should be the full usual display: given names then family "
        "(e.g. \"Maria Jose Garcia Lopez\"). If the form uses \"FAMILY, GIVEN\" with "
        "two surnames before the comma, both surnames belong in family_name.\n"
        "- For **items**, each row is one fact with **category** and **entity_role** "
        "when helpful:\n"
        "  entity_role examples: patient | ordering_provider | referring_provider | "
        "lab_facility | clinician_other | unknown\n"
        "- Categories for items: demographic | medication | lab_result | order | "
        "problem | provider | facility | other\n\n"
        "Return ONLY valid JSON (no markdown, no commentary) with this exact shape:\n"
        "{\n"
        '  "schema_version": 2,\n'
        '  "demographics": {\n'
        '    "patient_name": "",\n'
        '    "given_name": "",\n'
        '    "family_name": "",\n'
        '    "date_of_birth": "",\n'
        '    "sex": "",\n'
        '    "mrn": "",\n'
        '    "age": "",\n'
        '    "address": "",\n'
        '    "phone": ""\n'
        "  },\n"
        '  "providers_and_roles": {\n'
        '    "ordering_provider": "",\n'
        '    "referring_provider": "",\n'
        '    "lab_facility": ""\n'
        "  },\n"
        '  "items": [\n'
        "    {\n"
        '      "id": "1",\n'
        '      "raw_text": "verbatim or lightly cleaned snippet from OCR",\n'
        '      "category": "lab_result",\n'
        '      "entity_role": "patient",\n'
        '      "code_system": "",\n'
        '      "code": "",\n'
        '      "fhir_resource_type": "Observation"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Use empty strings for unknown fields. If nothing clinical is found, "
        'use empty strings in demographics/providers_and_roles and "items": [].\n\n'
        "OCR text:\n\n"
        f"{ocr_text}\n"
    )


def _call_llm(prompt: str, model: str | None = None) -> str:
    """
    Call a local LLM (e.g., via Ollama) and return the raw content string.
    """
    model_name = model or LLM_MODEL
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def run_llm_extraction(doc_stem: str, model: str | None = None) -> pathlib.Path:
    """
    Run LLM-based extraction for a given document stem.

    Writes schema v2 JSON: demographics, providers_and_roles, items.
    """
    ocr_path = OCR_DIR / f"{doc_stem}.txt"
    if not ocr_path.exists():
        raise FileNotFoundError(f"OCR text file not found: {ocr_path}")

    text = ocr_path.read_text(encoding="utf-8")
    snippet = text[: max(1000, LLM_OCR_CHARS)]

    prompt = _build_prompt(snippet)
    content = _call_llm(prompt, model=model)
    items, demographics, providers = _parse_llm_document(content)

    REVIEW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REVIEW_DATA_DIR / f"{doc_stem}_llm.json"

    payload = {
        "schema_version": 2,
        "demographics": demographics,
        "providers_and_roles": providers,
        "items": items,
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


if __name__ == "__main__":  # pragma: no cover - manual helper
    import argparse

    parser = argparse.ArgumentParser(
        description="Run local LLM-based clinical extraction on OCR text."
    )
    parser.add_argument("doc_stem", type=str, help="Document base name, e.g. 'Labs'.")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional model name to override LLM_MODEL.",
    )
    args = parser.parse_args()

    output = run_llm_extraction(args.doc_stem, model=args.model)
    print(f"LLM extraction saved to: {output}")
