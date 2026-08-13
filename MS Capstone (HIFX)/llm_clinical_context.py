"""
llm_clinical_context.py
-----------------------

Loads optional **clinical vocabulary hints** for the Ollama prompt. This is a
lightweight alternative to fine-tuning: plain text you can extend with jargon,
abbreviations, and section cues so the model classifies OCR lines more reliably.

Configure with environment variables (see .env.example).
"""

from __future__ import annotations

import os
import pathlib
from typing import Final

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent

_DEFAULT_FILE: Final[pathlib.Path] = _PROJECT_ROOT / "resources" / "clinical_context_for_llm.txt"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _strip_comment_lines(text: str) -> str:
    """Drop `# ...` comment lines; keep `##` markdown-style headings."""

    def _is_hash_comment(s: str) -> bool:
        if not s.startswith("#"):
            return False
        if s.startswith("##"):
            return False
        return True

    out_lines: list[str] = []
    for ln in text.splitlines():
        if _is_hash_comment(ln.strip()):
            continue
        out_lines.append(ln)
    return "\n".join(out_lines).strip()


def load_clinical_context_for_llm() -> str:
    """
    Return glossary text for the LLM system/user prompt, or empty string.

    Skipped when SKIP_LLM_CLINICAL_CONTEXT is set. Path override:
    LLM_CLINICAL_CONTEXT_PATH (absolute or relative to project root).
    Truncated to LLM_CLINICAL_CONTEXT_CHARS (default 16000) to protect context window.
    """
    if _env_bool("SKIP_LLM_CLINICAL_CONTEXT"):
        return ""

    raw_path = os.environ.get("LLM_CLINICAL_CONTEXT_PATH", "").strip()
    path = pathlib.Path(raw_path) if raw_path else _DEFAULT_FILE
    if not path.is_absolute():
        path = _PROJECT_ROOT / path

    if not path.is_file():
        return ""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    text = _strip_comment_lines(text)
    if not text:
        return ""

    max_chars = int(os.environ.get("LLM_CLINICAL_CONTEXT_CHARS", "16000").strip() or "16000")
    max_chars = max(500, min(max_chars, 100_000))
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[… clinical context truncated; raise LLM_CLINICAL_CONTEXT_CHARS …]"

    return text


def clinical_context_prompt_block() -> str:
    """
    Ready-to-embed section for the extraction prompt, or empty if disabled / missing file.
    """
    body = load_clinical_context_for_llm()
    if not body:
        return ""
    return (
        "**Clinical vocabulary reference** (use only to interpret OCR wording and "
        "choose categories such as lab_result, medication, problem, or order; "
        "do **not** treat this list as patient facts — every extracted value must "
        "still appear in the OCR block below):\n\n"
        f"{body}\n"
    )
