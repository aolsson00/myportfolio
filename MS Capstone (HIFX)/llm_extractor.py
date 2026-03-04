"""
llm_extractor.py
----------------

Optional helper that uses a local LLM (e.g., via Ollama) to turn OCR text
into structured clinical data points for review.

Workflow:
- Read `temp_extractions/<doc_stem>.txt`.
- Send a prompt to a local LLM HTTP endpoint.
- Expect a JSON list of items with fields compatible with `ExtractedItem`.
- Save the list to `review_data/<doc_stem>_llm.json`.

This keeps everything local and free; you just need a local model server
such as Ollama running on http://localhost:11434.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, List

import requests


BASE_DIR = pathlib.Path(".").resolve()
OCR_DIR = BASE_DIR / "temp_extractions"
REVIEW_DATA_DIR = BASE_DIR / "review_data"

# You can change these via environment variables if needed
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3")  # e.g. `llama3`, `mistral`, etc.


def _build_prompt(ocr_text: str) -> str:
    """
    Construct a concise, instruction-following prompt for clinical extraction.
    """
    return (
        "You are helping with a health informatics capstone.\n"
        "You will receive OCR text from a scanned clinical document.\n"
        "Extract only clinically relevant data points, such as:\n"
        "- Patient demographics (name, DOB, MRN, sex, age)\n"
        "- Medications (name, dose, route, frequency)\n"
        "- Lab results (test name, value, units)\n"
        "- Lab or imaging orders\n"
        "- Problem list / diagnoses\n\n"
        "Return ONLY valid JSON (no commentary) in the following format:\n"
        "[\n"
        "  {\n"
        '    \"id\": \"1\",\n'
        '    \"raw_text\": \"original line or snippet\",\n'
        '    \"category\": \"demographic\" | \"medication\" | \"lab_result\" | \"order\" | \"problem\",\n'
        '    \"code_system\": \"\",\n'
        '    \"code\": \"\",\n'
        '    \"fhir_resource_type\": \"Patient\" | \"MedicationStatement\" | \"Observation\" | \"ServiceRequest\" | \"Condition\" | \"\"\n'
        "  },\n"
        "  ...\n"
        "]\n\n"
        "If nothing useful is found, return an empty list [].\n\n"
        "Here is the OCR text:\n\n"
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
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # Ollama chat API: `message` -> `content`
    return data["message"]["content"]


def _parse_items(raw_content: str) -> List[dict[str, Any]]:
    """
    Parse JSON list from the model output, being tolerant of minor wrapping.
    """
    # Try direct JSON load first
    raw_content = raw_content.strip()
    try:
        parsed = json.loads(raw_content)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: try to locate the first '[' and last ']' and parse that slice
    start = raw_content.find("[")
    end = raw_content.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(raw_content[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # If all else fails, return empty list
    return []


def run_llm_extraction(doc_stem: str, model: str | None = None) -> pathlib.Path:
    """
    Run LLM-based extraction for a given document stem.

    Parameters
    ----------
    doc_stem : str
        Base name of the document (e.g. \"Labs\" for `Labs.PDF`).
    model : str, optional
        Model name to send to the local LLM server. Defaults to LLM_MODEL.

    Returns
    -------
    pathlib.Path
        Path to the JSON file containing extracted items.
    """
    ocr_path = OCR_DIR / f"{doc_stem}.txt"
    if not ocr_path.exists():
        raise FileNotFoundError(f"OCR text file not found: {ocr_path}")

    text = ocr_path.read_text(encoding="utf-8")
    # Truncate to a reasonable length to avoid context overflow
    snippet = text[:8000]

    prompt = _build_prompt(snippet)
    content = _call_llm(prompt, model=model)
    items = _parse_items(content)

    REVIEW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REVIEW_DATA_DIR / f"{doc_stem}_llm.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)

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

