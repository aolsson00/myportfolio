"""
patient_chart.py
----------------

Extracts patient demographics (name, DOB, MRN, sex, etc.) from OCR text
and writes an EHR-ready JSON file for creating a basic chart in an EHR system
(e.g., OpenEMR).

The output file is structured for easy import: flat fields plus an optional
FHIR Patient–style section for interoperability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(".").resolve()
OCR_DIR = BASE_DIR / "temp_extractions"
REVIEW_DATA_DIR = BASE_DIR / "review_data"
EHR_READY_DIR = BASE_DIR / "ehr_ready"


@dataclass
class PatientChart:
    """
    Basic patient information needed to create a chart in an EHR.
    """

    patient_name: str = ""
    given_name: str = ""
    family_name: str = ""
    date_of_birth: str = ""
    sex: str = ""
    mrn: str = ""
    age: str = ""
    address: str = ""
    phone: str = ""
    ordering_provider: str = ""
    referring_provider: str = ""
    lab_facility: str = ""
    source_document: str = ""

    def to_ehr_payload(self) -> dict:
        """
        Flat structure for EHR import (OpenEMR, etc.).
        """
        return {
            "patient_name": self.patient_name.strip(),
            "given_name": self.given_name.strip(),
            "family_name": self.family_name.strip(),
            "date_of_birth": self.date_of_birth.strip(),
            "sex": self.sex.strip(),
            "mrn": self.mrn.strip(),
            "age": self.age.strip(),
            "address": self.address.strip(),
            "phone": self.phone.strip(),
            "ordering_provider": self.ordering_provider.strip(),
            "referring_provider": self.referring_provider.strip(),
            "lab_facility": self.lab_facility.strip(),
            "source_document": self.source_document.strip(),
        }

    def to_fhir_patient_snippet(self) -> dict:
        """
        Minimal FHIR R4 Patient–style structure for interoperability.

        When ``family_name`` and ``given_name`` are set (e.g. compound surnames),
        uses structured ``family`` + ``given`` without splitting the last word only.
        Otherwise uses ``name[0].text`` so two-part surnames are not misclassified.
        """
        gn = self.given_name.strip()
        fn = self.family_name.strip()
        display = self.patient_name.strip()
        name_obj: dict = {}
        if fn and gn:
            name_obj["family"] = fn
            name_obj["given"] = [p for p in gn.split() if p]
            if display:
                name_obj["text"] = display
        elif display:
            name_obj["text"] = display
        name_list = [name_obj] if name_obj else []

        return {
            "resourceType": "Patient",
            "name": name_list,
            "birthDate": self.date_of_birth.strip() or None,
            "gender": self.sex.strip().lower() if self.sex else None,
            "identifier": [
                {"system": "http://hospital.example/mrn", "value": self.mrn.strip()}
            ]
            if self.mrn
            else [],
        }


# Patterns ordered so patient-block fields win over header/footer.
# Precompiled for reuse (avoids re.compile on every line).
def _compile_patterns():
    raw = [
        # Multi-word given / compound surnames: "GARCIA LOPEZ, MARIA JOSE"
        (
            r"Patient\s+Name\s*[:\s]+([A-Za-z][A-Za-z\s'\-]+,\s*[A-Za-z][A-Za-z\s'\-]+)(?=\s|$)",
            "patient_name",
        ),
        (
            r"^\s*Name:\s*([A-Za-z][A-Za-z\s'\-]+,\s*[A-Za-z][A-Za-z\s'\-]+)(?=\s+Date\s+of\s+Birth|\s|\()",
            "patient_name",
        ),
        (r"Patient\s+Name\s*[:\s]+(.+)", "patient_name"),
        (r"^\s*Name:\s*(.+?)(?=\s+Date\s+of\s+Birth|\s*$)", "patient_name"),
        (r"Patient\s+ID\s*[:\s]+([A-Za-z0-9]+)", "mrn"),
        (r"\bAddress:\s*(\d+[^\n]+?)(?=\s+Patient ID:|\s*$)", "address"),
        (r"^\s*Address:\s*(.+)$", "address"),
        (r"^\s*DOB\s*[:\s]+(\S+)", "date_of_birth"),
        (r"Date\s+of\s+Birth\s*[:\s]+(\S+)", "date_of_birth"),
        (r"Legal\s+Sex:\s*(\w+)", "sex"),
        (r"^\s*Sex\s*[:\s]+(\w+)$", "sex"),
        (r"^\s*Gender\s*[:\s]+(\w+)$", "sex"),
        (r"\(\s*(\d{1,3})\s*years?\s*\)", "age"),
        (r"Legal\s+Sex:.*Phone:\s*([0-9\-\.]+)", "phone"),
        (r"^\s*Phone:\s*([0-9\-\.]+)", "phone"),
        (r"^\s*MRN\s*[:\s#]*(\S+)", "mrn"),
        (r"medical\s*record\s*(?:number)?\s*[:\s#]*(\S+)", "mrn"),
        (r"dob\s*[:\s]+(\S+)", "date_of_birth"),
        (r"birth\s*date\s*[:\s]+(\S+)", "date_of_birth"),
        (r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*", "date_of_birth"),
        (r"^\s*Age\s*[:\s]+(\d{1,3})$", "age"),
        (r"^\s*Address\s*[:\s]+(.+)$", "address"),
        (r"^\s*Phone\s*[:\s]+(.+)$", "phone"),
    ]
    return [(re.compile(p, re.IGNORECASE), f) for p, f in raw]


LABEL_PATTERNS = _compile_patterns()

# Regex-backed fields only (provider names come from LLM disambiguation).
_REGEX_FIELDS = frozenset(
    {
        "patient_name",
        "date_of_birth",
        "sex",
        "mrn",
        "age",
        "address",
        "phone",
    }
)

# Labels that often get concatenated onto the same line as city/state/ZIP — not part of street address.
_ADDRESS_TAIL_LABEL = re.compile(
    r"(?i)(?=[,;]?\s*(?:"
    r"Age\s*[:#]?|"
    r"Account\s*(?:Number|#|No\.?)?\s*[:#]?|"
    r"Acct\.?\s*(?:#|No\.?)?\s*[:#]?|"
    r"MRN\s*[:#]?|"
    r"Medical\s*Record(?:\s*(?:Number|#))?\s*[:#]?|"
    r"Phone\s*[:#]?|"
    r"DOB\s*[:#]?|"
    r"Date\s*of\s*Birth\s*[:#]?|"
    r"Sex\s*[:#]?|"
    r"Gender\s*[:#]?|"
    r"Patient\s*ID\s*[:#]?|"
    r"Chart\s*#?\s*[:#]?|"
    r"Member\s*(?:ID|#)?\s*[:#]?|"
    r"Encounter\s*#?\s*[:#]?"
    r"))"
)


def _sanitize_address(raw: str) -> str:
    """
    Keep only mailing-address text. Strip trailing metadata often pasted after
    city/state (Age, Account #, MRN, etc.) from OCR or LLM over-capture.
    """
    if not raw:
        return ""
    s = raw.strip()
    m = _ADDRESS_TAIL_LABEL.search(s)
    if m:
        s = s[: m.start()].rstrip(" ,;")
    # Run-on without comma: "... MD Age 24" or "... ZIP Account 123"
    m2 = re.search(
        r"(?i)\s+(?=Age\s*[:#]?\s*\d|Account\s*(?:Number)?\s*[:#]?|MRN\s*[:#]?)",
        s,
    )
    if m2:
        s = s[: m2.start()].rstrip(" ,;")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_llm_demographics_and_providers(doc_stem: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Load structured demographics + provider roles from review_data/<stem>_llm.json
    when schema v2 is present. Legacy list-only files return empty dicts.
    """
    path = REVIEW_DATA_DIR / f"{doc_stem}_llm.json"
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, {}
    if isinstance(data, list):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    demo = data.get("demographics") or {}
    prov = data.get("providers_and_roles") or {}
    if not isinstance(demo, dict):
        demo = {}
    if not isinstance(prov, dict):
        prov = {}
    return demo, prov


def _apply_llm_string_fields(chart: PatientChart, demo: dict, prov: dict) -> None:
    """Fill chart from LLM output (non-empty strings only)."""
    demo_map = {
        "patient_name": "patient_name",
        "given_name": "given_name",
        "family_name": "family_name",
        "date_of_birth": "date_of_birth",
        "sex": "sex",
        "mrn": "mrn",
        "age": "age",
        "address": "address",
        "phone": "phone",
    }
    for json_key, attr in demo_map.items():
        val = demo.get(json_key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            if attr == "address":
                s = _sanitize_address(s)
            setattr(chart, attr, s)

    prov_map = {
        "ordering_provider": "ordering_provider",
        "referring_provider": "referring_provider",
        "lab_facility": "lab_facility",
    }
    for json_key, attr in prov_map.items():
        val = prov.get(json_key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            setattr(chart, attr, s)


def _extract_value(line: str, pattern: re.Pattern, field: str) -> Optional[str]:
    m = pattern.search(line)
    if not m:
        return None
    value = m.group(1).strip()
    if field == "date_of_birth":
        if len(value) > 12:
            value = value[:12]
        if re.search(r"[A-Za-z]", value):
            return None
    if field == "address":
        value = _sanitize_address(value)
    return value if value else None


def _title_name_parts(raw: str) -> str:
    """Title-case a name segment; preserve hyphens (e.g. Smith-Jones)."""

    def cap_word(w: str) -> str:
        if not w:
            return w
        if "-" in w:
            return "-".join(cap_word(x) for x in w.split("-"))
        if len(w) == 1:
            return w.upper()
        return w[0].upper() + w[1:].lower()

    return " ".join(cap_word(w) for w in raw.split())


def _parse_comma_form_name(chart: PatientChart) -> None:
    """
    Parse 'FAMILY..., GIVEN...' (common on forms). Family side may include two
    surnames (e.g. 'Garcia Lopez'); given side may include middle names.
    Does not run if structured names were already filled (e.g. by LLM).
    """
    raw = (chart.patient_name or "").strip()
    if not raw or "," not in raw:
        return
    if chart.family_name.strip() and chart.given_name.strip():
        return
    parts = [p.strip() for p in raw.split(",", 1)]
    if len(parts) != 2:
        return
    family_raw, given_raw = parts[0], parts[1]
    if not family_raw or not given_raw:
        return
    chart.family_name = _title_name_parts(family_raw)
    chart.given_name = _title_name_parts(given_raw)
    chart.patient_name = f"{chart.given_name} {chart.family_name}".strip()


def _reconcile_structured_name(chart: PatientChart) -> None:
    """If both parts exist, keep display name = given + full family (compound OK)."""
    g = chart.given_name.strip()
    f = chart.family_name.strip()
    if g and f:
        chart.patient_name = f"{g} {f}".strip()


def _append_city_state_from_patient_id_line(lines: list[str], chart: PatientChart) -> None:
    """If a line has 'Patient ID: XXX City State Zip', append City State Zip to address."""
    for line in lines:
        m = re.search(r"Patient\s+ID\s*[:\s]+\S+\s+([A-Za-z][^.]+(?:\d{5}(?:-\d{4})?)?)", line, re.IGNORECASE)
        if m:
            suffix = m.group(1).strip()
            if re.search(r"\d{5}", suffix) and chart.address:
                chart.address = _sanitize_address(f"{chart.address}, {suffix}")
            break


def extract_patient_demographics(
    ocr_text: str,
    doc_stem: Optional[str] = None,
) -> PatientChart:
    """
    Parse OCR text for patient name, DOB, MRN, sex, age, address, phone,
    and provider/lab context when available.

    When ``doc_stem`` is set and ``review_data/<doc_stem>_llm.json`` exists
    (schema v2 from the local LLM), those values are applied **first** so the
    model can distinguish patient vs ordering provider vs lab facility; regex
    rules then **fill any fields still empty**.
    """
    chart = PatientChart()
    if doc_stem:
        demo, prov = _load_llm_demographics_and_providers(doc_stem)
        _apply_llm_string_fields(chart, demo, prov)

    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]

    for line in lines:
        for pattern, field_name in LABEL_PATTERNS:
            if field_name not in _REGEX_FIELDS:
                continue
            if field_name == "address" and ("Address 1" in line or "Address 2" in line):
                continue
            if field_name == "phone" and not chart.patient_name and "Legal Sex" not in line:
                continue
            val = _extract_value(line, pattern, field_name)
            if val and not getattr(chart, field_name):
                setattr(chart, field_name, val)
                break

    # Fallback: if we have a date pattern but no DOB yet, take first valid date-like token
    if not chart.date_of_birth:
        for line in lines:
            m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", line)
            if m and not re.search(r"[A-Za-z]", m.group(1)):
                chart.date_of_birth = m.group(1)
                break

    _parse_comma_form_name(chart)
    _reconcile_structured_name(chart)

    # Append city/state/zip from "Patient ID: E108908723 Frederick MD 21703-7836" line
    _append_city_state_from_patient_id_line(lines, chart)

    chart.address = _sanitize_address(chart.address)

    return chart


def build_patient_chart(
    doc_stem: str,
    source_document: str = "",
    ocr_text: str | None = None,
) -> Path:
    """
    Read OCR text for the document (or use provided text), extract demographics,
    and write an EHR-ready JSON file.

    Parameters
    ----------
    doc_stem : str
        Base name of the document (e.g. "Labs" for Labs.PDF).
    source_document : str
        Optional display name for the source (e.g. "Labs.PDF").
    ocr_text : str, optional
        If provided, use this instead of reading from temp_extractions/<doc_stem>.txt.
        Avoids a second disk read when the caller already has the OCR output.

    Returns
    -------
    Path
        Path to the written file: ehr_ready/<doc_stem>_patient_chart.json
    """
    if ocr_text is None:
        ocr_path = OCR_DIR / f"{doc_stem}.txt"
        if not ocr_path.exists():
            raise FileNotFoundError(f"OCR text file not found: {ocr_path}")
        ocr_text = ocr_path.read_text(encoding="utf-8")

    chart = extract_patient_demographics(ocr_text, doc_stem=doc_stem)
    chart.source_document = source_document or f"{doc_stem}.pdf"

    EHR_READY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EHR_READY_DIR / f"{doc_stem}_patient_chart.json"

    payload = {
        "ehr_ready": chart.to_ehr_payload(),
        "fhir_patient": chart.to_fhir_patient_snippet(),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def get_patient_chart_path(doc_stem: str) -> Optional[Path]:
    """Return path to patient chart JSON if it exists."""
    p = EHR_READY_DIR / f"{doc_stem}_patient_chart.json"
    return p if p.exists() else None


# Attribute keys used for confirmation form and writing from user-confirmed values.
CHART_ATTRS = (
    "patient_name",
    "given_name",
    "family_name",
    "date_of_birth",
    "sex",
    "mrn",
    "age",
    "address",
    "phone",
    "ordering_provider",
    "referring_provider",
    "lab_facility",
)


def write_patient_chart_from_values(
    doc_stem: str,
    source_document: str,
    **kwargs: str,
) -> Path:
    """
    Build and write the EHR-ready JSON from user-confirmed (and optionally
    edited) attribute values. Keys should match CHART_ATTRS.
    """
    chart = PatientChart(
        patient_name=kwargs.get("patient_name", ""),
        given_name=kwargs.get("given_name", ""),
        family_name=kwargs.get("family_name", ""),
        date_of_birth=kwargs.get("date_of_birth", ""),
        sex=kwargs.get("sex", ""),
        mrn=kwargs.get("mrn", ""),
        age=kwargs.get("age", ""),
        address=kwargs.get("address", ""),
        phone=kwargs.get("phone", ""),
        ordering_provider=kwargs.get("ordering_provider", ""),
        referring_provider=kwargs.get("referring_provider", ""),
        lab_facility=kwargs.get("lab_facility", ""),
        source_document=source_document,
    )
    EHR_READY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EHR_READY_DIR / f"{doc_stem}_patient_chart.json"
    payload = {
        "ehr_ready": chart.to_ehr_payload(),
        "fhir_patient": chart.to_fhir_patient_snippet(),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return out_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build EHR-ready patient chart from OCR text."
    )
    parser.add_argument("doc_stem", type=str, help="Document base name, e.g. 'Labs'.")
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Source document name for the chart (e.g. Labs.PDF).",
    )
    args = parser.parse_args()

    path = build_patient_chart(args.doc_stem, source_document=args.source)
    print(f"Patient chart written to: {path}")
