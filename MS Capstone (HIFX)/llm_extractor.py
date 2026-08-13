"""
llm_extractor.py
----------------

Helper that uses a local LLM (e.g., via Ollama) to turn OCR text into:
- Structured **demographics** (patient vs provider disambiguation)
- **Provider / lab roles** (ordering clinician, facility, referring provider)
- **Clinical line items** for the Review screen

By default this runs automatically after OCR via ocr_engine.extract_text_from_pdf.
Set SKIP_LLM=1 to disable.

Output: `review_data/<doc_stem>_llm.json` — schema **v3** object with
``document_type``, ``additional_fields`` (dynamic review rows), ``demographics``,
``providers_and_roles``, and ``items`` (older v2 / list-only files still load where needed).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from typing import Any, List, Optional, Tuple

import requests

from fhir_gold_example import gold_fhir_prompt_block
from llm_clinical_context import clinical_context_prompt_block
from nlp_local import build_llm_nlp_summary
from text_cleaning import clean_clinical_ocr_text

LOGGER = logging.getLogger(__name__)


BASE_DIR = pathlib.Path(".").resolve()
OCR_DIR = BASE_DIR / "temp_extractions"
REVIEW_DATA_DIR = BASE_DIR / "review_data"

# You can change these via environment variables if needed
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3")  # e.g. `llama3`, `mistral`, etc.
# Characters of OCR text budgeted into the LLM prompt (after NLP prefix). Long clinical
# PDFs often exceed this; use head_tail mode to include start + end of the document.
LLM_OCR_CHARS = int(os.environ.get("LLM_OCR_CHARS", "32768"))

_MIDDLE_OMIT_MARKER = (
    "\n\n<<< --- OCR EXCERPT: middle of document omitted here (full OCR is longer; "
    "raise LLM_OCR_CHARS or use a model with a larger context window) --- >>>\n\n"
)


def _llm_ocr_excerpt_for_prompt(full_text: str, max_chars: int) -> str:
    """
    Build the OCR portion of the LLM prompt.

    - ``head``: only the beginning (legacy behavior when space is tight).
    - ``head_tail`` (default when truncated): first ~half and last ~half so later
      pages (impressions, signature blocks) are still visible to the model.
    """
    if max_chars <= 0 or len(full_text) <= max_chars:
        return full_text
    mode = os.environ.get("LLM_OCR_EXCERPT_MODE", "head_tail").strip().lower()
    if mode in ("head", "head_only", "start"):
        frag = full_text[:max_chars]
        LOGGER.info(
            "LLM OCR excerpt mode=head only; truncated %d of %d characters.",
            len(full_text) - max_chars,
            len(full_text),
        )
        return frag

    marker = _MIDDLE_OMIT_MARKER
    inner = max_chars - len(marker)
    if inner < 256:
        return full_text[:max_chars]
    head_len = inner // 2
    tail_len = inner - head_len
    out = full_text[:head_len] + marker + full_text[-tail_len:]
    LOGGER.info(
        "LLM OCR excerpt mode=head_tail: using first %d + last %d of %d OCR chars "
        "(%d chars omitted from middle).",
        head_len,
        tail_len,
        len(full_text),
        len(full_text) - head_len - tail_len,
    )
    return out


def _nlp_prefix_for_llm(full_text: str) -> str:
    if os.environ.get("NLP_LLM_SUMMARY", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return ""
    return build_llm_nlp_summary(full_text)


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


def _parse_llm_document(
    raw_content: str,
) -> Tuple[List[dict[str, Any]], dict, dict, Any, List[Any]]:
    """
    Parse LLM output into items + demographics + providers + document profile.
    Accepts v2/v3 JSON object, or legacy JSON array of items only.
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
        return parsed, {}, {}, None, []

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
        doc_type: Any = parsed.get("document_type")
        add_raw = parsed.get("additional_fields")
        additional_fields: List[Any] = add_raw if isinstance(add_raw, list) else []
        return items, demo, prov, doc_type, additional_fields

    return [], {}, {}, None, []


def _build_prompt(ocr_text: str) -> str:
    """
    Ask the model to separate patient vs providers vs lab context and emit strict JSON.
    """
    gold = gold_fhir_prompt_block()
    clinical_ctx = clinical_context_prompt_block()
    if clinical_ctx:
        LOGGER.info(
            "LLM prompt includes clinical vocabulary block (%d chars).",
            len(clinical_ctx),
        )
    return (
        "You are a clinical document analyst for a health informatics system.\n"
        "You receive noisy OCR text from scanned PDFs (Tesseract + EasyOCR). "
        "Your job is to DISAMBIGUATE roles and extract structured facts **strictly "
        "from that OCR text**.\n\n"
        "**Anti-hallucination:** Any reference FHIR Bundle above uses PLACEHOLDER_* "
        "values (fake). Never put PLACEHOLDER_* strings, and never copy example "
        "patient names/addresses from the reference into your output. Every "
        "demographic and provider string must be supported by the OCR block below "
        "(exact text or clear OCR paraphrase). If unsure, use empty string.\n\n"
        + clinical_ctx
        + ("\n" if clinical_ctx else "")
        + gold
        + "Rules:\n"
        "- **Patient** fields: only the person the record is **about**. "
        "Use labels like Patient, Name (next to DOB/MRN), Subject, or clearly "
        "demographic blocks. Do NOT put the ordering physician, signing doctor, "
        "lab director, or referral doctor into patient_name.\n"
        "- **Ordering provider** (labs/tests): who ordered the test — phrases like "
        "'Ordered by', 'Ordering provider', 'Physician:', on a requisition. Use the "
        "**name printed next to that label in OCR**, not the patient name and not any "
        "name from the reference FHIR example. If the line says 'Physician: Jane Doe', "
        "ordering_provider is Jane Doe. If unclear, leave empty.\n"
        "- **Referring provider**: 'Referring physician', 'PCP', 'sent by' when distinct "
        "from ordering.\n"
        "- **Lab / facility**: facility name on letterhead, 'Performed at', 'Lab:', "
        "hospital name on a report — not the patient's home address.\n"
        "- **Address** (strict): only **street, city, state, ZIP** (postal address). "
        "Never append Age, Account Number, MRN, Phone, DOB, Sex, or other labels to "
        "the address string — put those in **age**, **mrn**, **phone**, etc. If OCR "
        "runs several fields on one line, split them into the correct JSON keys only.\n"
        "- **address_structured** (FHIR R4 Address): When you can infer components, "
        "fill this object for Patient.address (use is almost always \"home\"). "
        "Use **line** as string array for street line(s); **city**, **state**, "
        "**postalCode**, **country** (e.g. USA) when visible. Leave unused keys as "
        "empty strings or omit. Also set **address** to a single human-readable line "
        "(comma-separated) for the confirmation form.\n"
        "- **Phone**: digits and formatting only; no other labels in the same string.\n"
        "- **Address / phone** sourcing: prefer the **patient's** home/contact when labeled; "
        "if only a facility address is visible, leave patient address empty and "
        "mention facility under lab_facility.\n"
        "- **Age / DOB**: extract both when present; do not guess.\n"
        "- **Name parts (required split):** **given_name** = first name only (one "
        "token unless hyphenated e.g. \"Mary-Kate\"). **middle_name** = middle name(s) "
        "or empty if none. **family_name** = full surname: one token (\"Smith\") or "
        "compound (\"Garcia Lopez\", \"Smith-Jones\"). **patient_name** = full "
        "display \"Given [Middle] Family\" spaced like the document. "
        "Do **not** put surnames in given_name or middle_name; do **not** put given "
        "names in family_name. If the form uses **LAST, FIRST MIDDLE** after a comma, "
        "everything before the comma is family_name (may be two surnames); after the "
        "comma, first token is given_name, rest is middle_name.\n"
        "- For **items**, each row is one fact with **category** and **entity_role** "
        "when helpful:\n"
        "  entity_role examples: patient | ordering_provider | referring_provider | "
        "lab_facility | clinician_other | unknown\n"
        "- Categories for items: demographic | medication | lab_result | order | "
        "problem | provider | facility | other\n"
        "- **Long / multi-page summaries:** The OCR section may include only the **start "
        "and end** of a long PDF (with an explicit \"middle omitted\" marker). Read "
        "**both** segments. Demographics may be in a header; medications, problems, "
        "and assessment often appear later — populate **additional_fields** and "
        "**items** generously from **all** OCR text you receive, not only the first "
        "page.\n"
        "- **Legacy layouts (med rec, labs, forms, fax scans):** The prompt may include "
        "a **Clinical vocabulary reference** with archetypes such as: medication "
        "reconciliation lists (start/stopped/completed dates), garbled **lab tables** "
        "(do not invent analytes/results), checkbox intake noise (`|`, `[`, `]`), "
        "multi-page **discharge summaries** (deduplicate repeated hospital-course lines; "
        "capture discharge meds/follow-up from tail sections), and **skewed fax** ED "
        "notes (extract only clearly readable fragments). Ignore banner lines that say "
        "the document is synthetic or has no real PHI unless you still see real "
        "demographic lines below them.\n"
        "- **Document type:** Set **document_type** with **code** (short machine id, "
        "e.g. laboratory_report, colonoscopy_report, imaging_report, visit_summary) "
        "and **display** (human-readable). Infer from layout and headings.\n"
        "- **additional_fields:** Besides fixed demographics, add **every clinically "
        "useful fact** you can tie to this document (lab analyte + result + unit/ref, "
        "procedure findings, impression, indication, ICD codes, report date, etc.). "
        "Use one object per fact with **key** (snake_case id), **label** (short UI "
        "title), **value** (what the patient/chart should store), optional "
        "**source_hint** (where in OCR), and **fhir_resource_hint** "
        "(Observation | Condition | Procedure | DiagnosticReport | DocumentReference "
        "| MedicationStatement | AllergyIntolerance | FamilyMemberHistory | other). "
        "Do **not** duplicate the fixed demographics keys; use additional_fields for "
        "everything else worth confirming. Omit facts you cannot support from OCR.\n\n"
        "Return ONLY valid JSON (no markdown, no commentary) with this exact shape:\n"
        "{\n"
        '  "schema_version": 3,\n'
        '  "document_type": {\n'
        '    "code": "laboratory_report",\n'
        '    "display": "Laboratory report"\n'
        "  },\n"
        '  "additional_fields": [\n'
        "    {\n"
        '      "key": "h_pylori_result",\n'
        '      "label": "H. pylori test result",\n'
        '      "value": "Positive",\n'
        '      "source_hint": "",\n'
        '      "fhir_resource_hint": "Observation"\n'
        "    }\n"
        "  ],\n"
        '  "demographics": {\n'
        '    "patient_name": "",\n'
        '    "given_name": "",\n'
        '    "middle_name": "",\n'
        '    "family_name": "",\n'
        '    "date_of_birth": "",\n'
        '    "sex": "",\n'
        '    "mrn": "",\n'
        '    "age": "",\n'
        '    "address": "",\n'
        '    "address_structured": {\n'
        '      "use": "home",\n'
        '      "line": ["123 Main St"],\n'
        '      "city": "",\n'
        '      "state": "",\n'
        '      "postalCode": "",\n'
        '      "country": ""\n'
        "    },\n"
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
        "Use **schema_version** 3. Use empty strings for unknown fixed fields. "
        'If document type is unclear use code \"generic_clinical_document\". '
        '**additional_fields** may be [] when nothing extra is present beyond demographics. '
        'If nothing clinical is found, '
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

    Writes schema v3 JSON (demographics, document_type, additional_fields, etc.).
    """
    ocr_path = OCR_DIR / f"{doc_stem}.txt"
    if not ocr_path.exists():
        raise FileNotFoundError(f"OCR text file not found: {ocr_path}")

    text = clean_clinical_ocr_text(ocr_path.read_text(encoding="utf-8"))
    ocr_body = _llm_ocr_excerpt_for_prompt(text, LLM_OCR_CHARS)
    snippet = _nlp_prefix_for_llm(text) + ocr_body

    prompt = _build_prompt(snippet)
    content = _call_llm(prompt, model=model)
    items, demographics, providers, doc_type, additional_fields = _parse_llm_document(
        content
    )

    REVIEW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REVIEW_DATA_DIR / f"{doc_stem}_llm.json"

    dt_obj: dict[str, Any]
    if isinstance(doc_type, dict):
        dt_obj = doc_type
    elif isinstance(doc_type, str) and doc_type.strip():
        dt_obj = {
            "code": re.sub(r"[^a-zA-Z0-9_]+", "_", doc_type.strip().lower()).strip("_")
            or "generic_clinical_document",
            "display": doc_type.strip(),
        }
    else:
        dt_obj = {
            "code": "generic_clinical_document",
            "display": "Clinical document",
        }

    payload = {
        "schema_version": 3,
        "document_type": dt_obj,
        "additional_fields": additional_fields,
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
