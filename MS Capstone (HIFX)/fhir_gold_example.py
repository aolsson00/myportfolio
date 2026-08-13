"""
fhir_gold_example.py
--------------------

Load a **gold-standard FHIR R4 Bundle** JSON file for **few-shot prompting** the
local LLM (Ollama). This is *in-context learning*, not weight fine-tuning: the
model sees a perfect target shape so extractions align with your export.

**spaCy** cannot be \"trained\" from one FHIR file without a labeled corpus;
use this gold file with the **LLM**, or add spaCy EntityRuler patterns separately.

Environment
-----------
FHIR_GOLD_EXAMPLE_PATH   Path to Bundle JSON (default: examples/gold_fhir_bundle.json)
SKIP_FHIR_GOLD_PROMPT  Set to 1 to omit the gold FHIR block from LLM prompts
FHIR_GOLD_MAX_CHARS      Max characters of JSON to inject (default 12000)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent


def default_gold_path() -> Path:
    return _BASE / "examples" / "gold_fhir_bundle.json"


def load_gold_fhir_bundle_text() -> str:
    """
    Read and minify/validate JSON; return compact string for prompts.
    Returns empty string if disabled, missing, or invalid.
    """
    if os.environ.get("SKIP_FHIR_GOLD_PROMPT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return ""

    raw_path = os.environ.get("FHIR_GOLD_EXAMPLE_PATH", "").strip()
    path = Path(raw_path) if raw_path else default_gold_path()
    if not path.is_file():
        LOGGER.debug("Gold FHIR example not found at %s — prompt will omit it.", path)
        return ""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Could not read gold FHIR example from %s: %s", path, exc)
        return ""

    if not isinstance(data, dict) or data.get("resourceType") != "Bundle":
        LOGGER.warning("Gold FHIR file must be a FHIR Bundle object: %s", path)
        return ""

    text = json.dumps(data, ensure_ascii=False)
    max_chars = int(os.environ.get("FHIR_GOLD_MAX_CHARS", "12000"))
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… [truncated for prompt size; raise FHIR_GOLD_MAX_CHARS]"
        LOGGER.info("Gold FHIR example truncated to %s characters.", max_chars)

    return text


def gold_fhir_prompt_block() -> str:
    """
    Human-readable section to prepend to the LLM user prompt.
    """
    bundle_json = load_gold_fhir_bundle_text()
    if not bundle_json:
        return ""

    return (
        "## FHIR Bundle — **STRUCTURE ONLY** (not patient data)\n"
        "The JSON below shows **resource shapes** your app exports (Patient, "
        "Practitioner, Organization, bundle entry layout). Names, MRNs, DOBs, "
        "phones, and addresses in it are **PLACEHOLDER_* tokens — synthetic and "
        "NOT REAL**.\n\n"
        "**CRITICAL:** Your extraction MUST come **only** from the **OCR text** at "
        "the end of this prompt. **Never** copy or infer patient demographics from "
        "this reference Bundle. If a field is not clearly present in OCR, leave it "
        "empty. Copying placeholder or example values into `demographics` is a "
        "serious error.\n\n"
        "```json\n"
        f"{bundle_json}\n"
        "```\n\n"
        "Your **required output** is the schema_version 3 JSON object (document_type, "
        "additional_fields, demographics, providers_and_roles, items) — **not** this "
        "FHIR verbatim. Use the Bundle only to understand **roles** (who is patient "
        "vs ordering vs referring vs lab) and **field separation**.\n\n"
    )
