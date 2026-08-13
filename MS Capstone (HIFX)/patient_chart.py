"""
patient_chart.py
----------------

Extracts patient demographics (name, DOB, MRN, sex, etc.) from OCR text
and writes a **FHIR R4** document (JSON) for import into EHR systems or FHIR servers.

Output: ``ehr_ready/<doc_stem>_chart.fhir.json`` — a ``Bundle`` (type ``collection``)
with ``Patient`` and optional ``Practitioner`` / ``Organization`` entries.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from clinical_lexicon import guess_category_from_keywords
from text_cleaning import clean_clinical_ocr_text

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(".").resolve()
OCR_DIR = BASE_DIR / "temp_extractions"
REVIEW_DATA_DIR = BASE_DIR / "review_data"
EHR_READY_DIR = BASE_DIR / "ehr_ready"
PROCESSED_SCANS_DIR = BASE_DIR / "processed_scans"
REVIEW_LOGS_DIR = BASE_DIR / "review_logs"

# Local extension URLs for OCR-derived context (non-normative; replace for production).
FHIR_STRUCTURE_DEF_BASE = "http://example.org/fhir/StructureDefinition"


def ehr_fhir_output_path(doc_stem: str) -> Path:
    """Confirmed chart export: FHIR Bundle as JSON (``.fhir.json``)."""
    return EHR_READY_DIR / f"{doc_stem}_chart.fhir.json"


def _fhir_safe_id(doc_stem: str, suffix: str) -> str:
    """FHIR id allows [A-Za-z0-9-.]{1,64}."""
    raw = re.sub(r"[^A-Za-z0-9\-.]", "-", doc_stem).strip("-") or "doc"
    if suffix:
        raw = f"{raw}-{suffix}"
    return raw[:64]


def _normalize_fhir_date(raw: str) -> Optional[str]:
    """Return YYYY-MM-DD when parsable; otherwise None."""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mm, dd, yy = m.groups()
        y = int(yy)
        if y < 100:
            y += 2000 if y < 50 else 1900
        try:
            d = date(y, int(mm), int(dd))
            return d.isoformat()
        except ValueError:
            return None
    return None


def _parse_dob_flexible(raw: str) -> Optional[str]:
    """
    Normalize assorted OCR / prose DOB strings to YYYY-MM-DD for chart + FHIR.
    Accepts slash dates, ISO, and month-name forms (e.g. March 26, 2000).
    """
    s = raw.strip().strip(",").strip()
    if not s:
        return None
    iso = _normalize_fhir_date(s)
    if iso:
        return iso
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%m-%d-%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def compute_age_completed_years(
    dob_raw: str, as_of: Optional[date] = None
) -> Optional[int]:
    """
    Patient age in **completed** full years at ``as_of`` (default: local today),
    from a DOB string that ``_normalize_fhir_date`` can parse to YYYY-MM-DD.
    """
    iso = _normalize_fhir_date(dob_raw)
    if not iso:
        return None
    parts = iso.split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        birth = date(y, m, d)
    except ValueError:
        return None
    today = as_of or date.today()
    if birth > today:
        return None
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return max(0, years)


def age_display_from_dob(dob_raw: str, as_of: Optional[date] = None) -> str:
    """Non-empty string of integer years, or empty if DOB is unusable."""
    n = compute_age_completed_years(dob_raw, as_of=as_of)
    if n is None:
        return ""
    return str(n)


def merge_computed_age_into_chart_fields(chart: dict[str, str]) -> None:
    """Set ``age`` from ``date_of_birth`` when a numeric age can be derived."""
    computed = age_display_from_dob(chart.get("date_of_birth", ""))
    if computed:
        chart["age"] = computed


FHIR_ADDRESS_USE = frozenset({"home", "work", "temp", "old", "billing"})


def normalize_fhir_address_dict(raw: Any) -> Optional[dict[str, Any]]:
    """
    Normalize LLM / JSON input into a FHIR R4 Address element (one object).
    Returns None if nothing usable is present.
    """
    if raw is None or isinstance(raw, (int, float, bool)):
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None

    out: dict[str, Any] = {}
    u = raw.get("use")
    if isinstance(u, str) and u.strip().lower() in FHIR_ADDRESS_USE:
        out["use"] = u.strip().lower()
    else:
        out["use"] = "home"

    line = raw.get("line")
    if isinstance(line, list):
        lines = [str(x).strip() for x in line if str(x).strip()]
        if lines:
            out["line"] = lines
    elif isinstance(line, str) and line.strip():
        out["line"] = [line.strip()]

    for key in ("city", "district", "state", "postalCode", "country"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()

    tx = raw.get("text")
    if isinstance(tx, str) and tx.strip():
        out["text"] = tx.strip()

    if any(k in out for k in ("line", "city", "state", "postalCode", "text")):
        return out
    return None


def format_address_one_line(addr: dict[str, Any]) -> str:
    """Single-line display from structured address for the confirm form."""
    parts: list[str] = []
    for ln in addr.get("line") or []:
        if isinstance(ln, str) and ln.strip():
            parts.append(ln.strip())
    for key in ("city", "district", "state", "postalCode", "country"):
        v = addr.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if not parts and isinstance(addr.get("text"), str):
        return addr["text"].strip()
    return ", ".join(parts)


def _administrative_gender(sex: str) -> str:
    s = sex.strip().lower()
    if s in ("m", "male", "man"):
        return "male"
    if s in ("f", "female", "woman"):
        return "female"
    if s in ("o", "other", "nonbinary", "non-binary", "nb"):
        return "other"
    if s:
        return "other"
    return "unknown"


@dataclass
class PatientChart:
    """
    Basic patient information needed to create a chart in an EHR.
    """

    patient_name: str = ""
    given_name: str = ""
    middle_name: str = ""
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
    # When set (e.g. from LLM), exported as FHIR Patient.address with line/city/state/ZIP.
    address_structured: Optional[dict[str, Any]] = None

    def refresh_computed_age(self) -> None:
        """Overwrite ``age`` with years from ``date_of_birth`` vs today when parsable."""
        computed = age_display_from_dob(self.date_of_birth)
        if computed:
            self.age = computed

    def to_ehr_payload(self) -> dict:
        """
        Flat structure for EHR import (OpenEMR, etc.).
        """
        return {
            "patient_name": self.patient_name.strip(),
            "given_name": self.given_name.strip(),
            "middle_name": self.middle_name.strip(),
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
            "address_structured": self.address_structured or {},
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
        given_parts = _fhir_given_list(self)
        name_obj: dict = {}
        if fn and given_parts:
            name_obj["family"] = fn
            name_obj["given"] = given_parts
            if display:
                name_obj["text"] = display
        elif fn and gn:
            name_obj["family"] = fn
            name_obj["given"] = [gn]
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


def _fhir_given_list(chart: PatientChart) -> list[str]:
    """FHIR HumanName.given: first name, then middle (each may be multi-word)."""
    parts: list[str] = []
    g = (chart.given_name or "").strip()
    m = (chart.middle_name or "").strip()
    if g:
        parts.append(g)
    if m:
        parts.append(m)
    return parts


def _patient_addresses_for_fhir(chart: PatientChart) -> list[dict[str, Any]]:
    """FHIR R4 Patient.address[]: prefer structured (LLM) + optional ``text`` for display."""
    if chart.address_structured:
        norm = normalize_fhir_address_dict(chart.address_structured)
        if norm:
            merged: dict[str, Any] = dict(norm)
            if chart.address.strip():
                merged["text"] = chart.address.strip()
            return [merged]
    if chart.address.strip():
        return [{"use": "home", "text": chart.address.strip()}]
    return []


def build_fhir_collection_bundle(
    chart: PatientChart,
    doc_stem: str,
    *,
    document_type_code: str = "",
    document_type_display: str = "",
    confirmed_extra_fields: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Build a FHIR R4 Bundle (type collection) with Patient and optional
    Practitioner / Organization resources. Uses ``urn:uuid`` references within the bundle.

    ``confirmed_extra_fields`` lists user-confirmed key/value rows from the review UI;
    each becomes a simple ``Observation`` tied to the patient. Rows with empty
    ``value`` are skipped (caller should also omit \"exclude from FHIR\" fields).
    """
    confirmed_extra_fields = confirmed_extra_fields or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundle_id = str(uuid.uuid4())

    fn = chart.family_name.strip()
    display = chart.patient_name.strip()
    given_parts = _fhir_given_list(chart)
    name_obj: dict[str, Any] = {}
    if fn and given_parts:
        name_obj["family"] = fn
        name_obj["given"] = given_parts
        if display:
            name_obj["text"] = display
    elif fn and chart.given_name.strip():
        name_obj["family"] = fn
        name_obj["given"] = [chart.given_name.strip()]
        if display:
            name_obj["text"] = display
    elif display:
        name_obj["text"] = display
    name_list = [name_obj] if name_obj else []

    dob = _normalize_fhir_date(chart.date_of_birth)

    patient_resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": _fhir_safe_id(doc_stem, "patient"),
        "meta": {
            "lastUpdated": now,
            "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
        },
        "identifier": [
            {
                "use": "usual",
                "system": "http://hospital.example/mrn",
                "value": chart.mrn.strip(),
            }
        ]
        if chart.mrn.strip()
        else [],
        "name": name_list,
        "gender": _administrative_gender(chart.sex),
    }

    if dob:
        patient_resource["birthDate"] = dob

    telecom: list[dict[str, str]] = []
    if chart.phone.strip():
        telecom.append({"system": "phone", "value": chart.phone.strip()})
    if telecom:
        patient_resource["telecom"] = telecom

    addr_entries = _patient_addresses_for_fhir(chart)
    if addr_entries:
        patient_resource["address"] = addr_entries

    entries: list[dict[str, Any]] = []

    ref_ordering: Optional[str] = None
    if chart.ordering_provider.strip():
        uid = str(uuid.uuid4())
        ref_ordering = f"urn:uuid:{uid}"
        entries.append(
            {
                "fullUrl": ref_ordering,
                "resource": {
                    "resourceType": "Practitioner",
                    "id": _fhir_safe_id(doc_stem, "ordering"),
                    "name": [{"text": chart.ordering_provider.strip()}],
                },
            }
        )

    ref_referring: Optional[str] = None
    if chart.referring_provider.strip():
        uid = str(uuid.uuid4())
        ref_referring = f"urn:uuid:{uid}"
        entries.append(
            {
                "fullUrl": ref_referring,
                "resource": {
                    "resourceType": "Practitioner",
                    "id": _fhir_safe_id(doc_stem, "referring"),
                    "name": [{"text": chart.referring_provider.strip()}],
                },
            }
        )

    ref_lab_org: Optional[str] = None
    if chart.lab_facility.strip():
        uid = str(uuid.uuid4())
        ref_lab_org = f"urn:uuid:{uid}"
        entries.append(
            {
                "fullUrl": ref_lab_org,
                "resource": {
                    "resourceType": "Organization",
                    "id": _fhir_safe_id(doc_stem, "lab"),
                    "name": chart.lab_facility.strip(),
                },
            }
        )

    extensions: list[dict[str, Any]] = []
    if not dob and chart.date_of_birth.strip():
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/unparsed-birth-date",
                "valueString": chart.date_of_birth.strip(),
            }
        )
    if chart.source_document.strip():
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/source-document",
                "valueString": chart.source_document.strip(),
            }
        )
    if ref_referring:
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/referring-practitioner",
                "valueReference": {"reference": ref_referring},
            }
        )
    if ref_lab_org:
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/lab-organization",
                "valueReference": {"reference": ref_lab_org},
            }
        )
    if chart.age.strip():
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/age-years-text",
                "valueString": chart.age.strip(),
            }
        )
    if document_type_display.strip():
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/source-document-type",
                "valueString": document_type_display.strip()[:512],
            }
        )
    if document_type_code.strip():
        extensions.append(
            {
                "url": f"{FHIR_STRUCTURE_DEF_BASE}/source-document-type-code",
                "valueString": document_type_code.strip()[:128],
            }
        )
    if extensions:
        patient_resource["extension"] = extensions

    if ref_ordering:
        patient_resource["generalPractitioner"] = [
            {
                "reference": ref_ordering,
                "display": chart.ordering_provider.strip(),
            }
        ]

    patient_uid = str(uuid.uuid4())
    patient_full = f"urn:uuid:{patient_uid}"
    entries.append(
        {
            "fullUrl": patient_full,
            "resource": patient_resource,
        }
    )

    if confirmed_extra_fields:
        for i, row in enumerate(confirmed_extra_fields):
            val = str(row.get("value") or "").strip()
            if not val:
                continue
            label = str(row.get("label") or row.get("key") or "Additional information").strip()
            ouid = str(uuid.uuid4())
            ofull = f"urn:uuid:{ouid}"
            obs: dict[str, Any] = {
                "resourceType": "Observation",
                "id": _fhir_safe_id(doc_stem, f"add-{i}"),
                "meta": {
                    "lastUpdated": now,
                    "profile": ["http://hl7.org/fhir/StructureDefinition/Observation"],
                },
                "status": "final",
                "code": {"text": label},
                "subject": {"reference": patient_full},
                "valueString": val,
            }
            hint = str(row.get("fhir_resource_hint") or "").strip()
            if hint:
                obs["extension"] = [
                    {
                        "url": f"{FHIR_STRUCTURE_DEF_BASE}/llm-fhir-hint",
                        "valueString": hint[:256],
                    }
                ]
            entries.append({"fullUrl": ofull, "resource": obs})

    return {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {
            "lastUpdated": now,
            "profile": ["http://hl7.org/fhir/StructureDefinition/Bundle"],
        },
        "type": "collection",
        "timestamp": now,
        "entry": entries,
    }


# Patterns ordered so patient-block fields win over header/footer.
# Precompiled for reuse (avoids re.compile on every line).
def _compile_patterns():
    raw = [
        # ------------------------------------------------------------------
        # C-CDA / clinical summary / portal export (single-line header)
        # ------------------------------------------------------------------
        (
            r"(?i)^([^:\n]+?,\s*[A-Za-z0-9][A-Za-z0-9'\-\s]*?)(?=\s+Admin\s+Sex:)",
            "patient_name",
        ),
        (r"(?i)Admin\s+Sex:\s*(\w+)", "sex"),
        (r"(?i)\bGender\s*:\s*(Male|Female|M|F|Other|Unknown)\b", "sex"),
        (
            r"(?i)\bSex\s*:\s*(Male|Female|Intersex|Unknown|M|F)\b(?=\s|DOB:|$)",
            "sex",
        ),
        # Inline DOB on same line as demographics header
        (r"(?i)\bDOB\s*:\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$", "date_of_birth"),
        (r"(?i)\bDOB\s*:\s*(\d{1,2}/\d{1,2}/\d{2,4})(?=\s|$)", "date_of_birth"),
        # Member ID line (not "Document ID:" with OID)
        (r"(?i)^ID:\s*(\d{5,})\s*$", "mrn"),
        # Summary-type US address line (digits + street … city, ST ZIP…)
        (
            r"(?i)^(\d+\s+[A-Z0-9].+,\s*[^,\n]+,\s*[A-Z]{2}\s*[\d\-]{4,12}(?:,\s*[A-Z]{2,})?)\s*$",
            "address",
        ),
        (r"(?i)Attending\s+Physician:\s*(.+)$", "ordering_provider"),
        # Multi-word given / compound surnames: "GARCIA LOPEZ, MARIA JOSE"
        (
            r"(?:Patient|PT)\s+Name\s*[:\s]+([A-Za-z][A-Za-z\s'\-]+,\s*[A-Za-z][A-Za-z\s'\-]+)(?=\s|$)",
            "patient_name",
        ),
        (
            r"^\s*Name:\s*([A-Za-z][A-Za-z\s'\-]+,\s*[A-Za-z][A-Za-z\s'\-]+)(?=\s+Date\s+of\s+Birth|\s|\()",
            "patient_name",
        ),
        (r"(?:Patient|PT)\s+Name\s*[:\s]+(.+)", "patient_name"),
        (r"^\s*Name:\s*(.+?)(?=\s+Date\s+of\s+Birth|\s*$)", "patient_name"),
        (r"Patient\s+ID\s*[:\s]+([A-Za-z0-9]+)", "mrn"),
        (r"\bAddress:\s*(\d+[^\n]+?)(?=\s+Patient ID:|\s*$)", "address"),
        # Forms often use "Address 1" / "Address Line 1" (previously skipped entirely).
        (
            r"(?i)^\s*Address\s*(?:Line\s*)?(?:1|One|I)\s*[:\s#]+(.+)$",
            "address",
        ),
        (r"^\s*Address:\s*(.+)$", "address"),
        (r"(?i)Date\s+of\s+Birth\s*[:\s]+(.+)$", "date_of_birth"),
        (r"(?i)^\s*DOB\s*[:\s]+(.+)$", "date_of_birth"),
        (r"Legal\s+Sex:\s*(\w+)", "sex"),
        (r"(?i)Biological\s+Sex\s*[:\s]+\s*(\w+)", "sex"),
        (r"^\s*Sex\s*[:\s]+\s*(\w+)", "sex"),
        (r"^\s*Gender\s*[:\s]+\s*(\w+)", "sex"),
        (r"\(\s*(\d{1,3})\s*years?\s*\)", "age"),
        (r"Legal\s+Sex:.*Phone:\s*([0-9\-\.]+)", "phone"),
        (
            r"(?i)\bphone(?:\s+number)?\s+of\s+patient\s*[:\s]+([\d\-\.\s]+)",
            "phone",
        ),
        (r"^\s*Phone:\s*([\d\-\.\s\(\)]+)", "phone"),
        (r"(?i)^\s*(?:Tel|Telephone|Mobile|Cell|Cellular)\s*(?:#|Number|No\.?)?\s*[:\s]+\s*([\d\-\.\s\(\)]+)", "phone"),
        (r"^\s*MRN\s*[:\s#]*(\S+)", "mrn"),
        (r"medical\s*record\s*(?:number)?\s*[:\s#]*(\S+)", "mrn"),
        (r"(?i)dob\s*[:\s]+(.+)$", "date_of_birth"),
        (r"(?i)birth\s*date\s*[:\s]+(.+)$", "date_of_birth"),
        (r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$", "date_of_birth"),
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
        "ordering_provider",
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
        "middle_name": "middle_name",
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

    _apply_llm_address_structured(chart, demo)


def _apply_llm_address_structured(chart: PatientChart, demo: dict) -> None:
    """Set ``address_structured`` (+ one-line ``address`` if empty) from LLM JSON."""
    norm = normalize_fhir_address_dict(demo.get("address_structured"))
    if not norm:
        return
    chart.address_structured = norm
    if not (chart.address or "").strip():
        chart.address = _sanitize_address(format_address_one_line(norm))


def _extract_value(line: str, pattern: re.Pattern, field: str) -> Optional[str]:
    m = pattern.search(line)
    if not m:
        return None
    value = m.group(1).strip()
    if field == "date_of_birth":
        if len(value) > 64:
            value = value[:64]
        parsed = _parse_dob_flexible(value)
        return parsed
    if field == "address":
        value = _sanitize_address(value)
    return value if value else None


# Label-only OCR lines (value printed on the next line).
_LABEL_ONLY_PATIENT_NAME = re.compile(r"(?i)^(patient|pt)\s+name\s*:?\s*$")
_LABEL_ONLY_NAME = re.compile(r"(?i)^name\s*:?\s*$")
_LABEL_ONLY_DOB = re.compile(r"(?i)^(date\s+of\s+birth|dob)\s*:?\s*$")
_LABEL_ONLY_SEX = re.compile(r"(?i)^(sex|gender)\s*:?\s*$")
_LABEL_ONLY_PHONE = re.compile(
    r"(?i)^(phone|tel|telephone|mobile|cell|cellular)(?:\s+(?:#|number|no\.?))?\s*:?\s*$"
)
_LABEL_ONLY_ADDRESS = re.compile(
    r"(?i)^address(?:\s+(?:line\s*)?(?:1|one))?\s*:?\s*$"
)


def _looks_like_demographic_field_label(line: str) -> bool:
    return bool(
        re.match(
            r"(?i)^(address|phone|mrn|dob|date|sex|gender|patient|pt\s+name|"
            r"name|age|physician|provider|lab|result|hospital|icd|member)\b",
            line,
        )
    )


def _merge_label_continuation_lines(lines: list[str]) -> list[str]:
    """Turn 'Patient Name' + next line 'John Smith' into one synthetic line for regex."""
    if not lines:
        return lines
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if (
                _LABEL_ONLY_PATIENT_NAME.match(ln)
                and nxt
                and not _looks_like_demographic_field_label(nxt)
            ):
                out.append(f"Patient Name: {nxt}")
                i += 2
                continue
            if (
                _LABEL_ONLY_NAME.match(ln)
                and nxt
                and not _looks_like_demographic_field_label(nxt)
            ):
                out.append(f"Name: {nxt}")
                i += 2
                continue
            if _LABEL_ONLY_DOB.match(ln) and nxt:
                lbl = ln.strip().rstrip(":")
                out.append(f"{lbl}: {nxt}")
                i += 2
                continue
            if (
                _LABEL_ONLY_SEX.match(ln)
                and nxt
                and not _looks_like_demographic_field_label(nxt)
            ):
                out.append(f"Sex: {nxt}")
                i += 2
                continue
            if _LABEL_ONLY_PHONE.match(ln) and nxt:
                out.append(f"Phone: {nxt}")
                i += 2
                continue
            if (
                _LABEL_ONLY_ADDRESS.match(ln)
                and nxt
                and not _looks_like_demographic_field_label(nxt)
            ):
                out.append(f"Address: {nxt}")
                i += 2
                continue
        out.append(ln)
        i += 1
    return out


# Lines that often contain a slash date but are not the patient's DOB.
_DOB_FALLBACK_SKIP = re.compile(
    r"(?i)(summary\s+of\s+care|summarization|episode\s+note|"
    r"created\s*:|source\s*:|report\s+date|printed\s*:|"
    r"\bto\s+\d{1,2}/|encounter\s+date|date\s+of\s+death)"
)


def _skip_line_for_dob_fallback(line: str) -> bool:
    return bool(_DOB_FALLBACK_SKIP.search(line))


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
    surnames (e.g. 'Garcia Lopez'); given side splits into first + middle.
    """
    raw = (chart.patient_name or "").strip()
    if not raw or "," not in raw:
        return
    parts = [p.strip() for p in raw.split(",", 1)]
    if len(parts) != 2:
        return
    family_raw, given_raw = parts[0], parts[1]
    if not family_raw or not given_raw:
        return
    if (
        chart.family_name.strip()
        and chart.given_name.strip()
        and chart.middle_name.strip()
    ):
        return
    if not chart.family_name.strip():
        chart.family_name = _title_name_parts(family_raw)
    if not chart.given_name.strip():
        gtoks = given_raw.split()
        if gtoks:
            chart.given_name = _title_name_parts(gtoks[0])
            if len(gtoks) > 1 and not chart.middle_name.strip():
                chart.middle_name = _title_name_parts(" ".join(gtoks[1:]))
    elif not chart.middle_name.strip() and len(given_raw.split()) > 1:
        gtoks = given_raw.split()
        if gtoks:
            chart.given_name = _title_name_parts(gtoks[0])
            chart.middle_name = _title_name_parts(" ".join(gtoks[1:]))


def _tokens_suffix_match(words: list[str], suffix: list[str]) -> bool:
    if len(suffix) > len(words):
        return False
    tail = words[-len(suffix) :]
    return all(a.casefold() == b.casefold() for a, b in zip(tail, suffix))


def _infer_from_word_count(chart: PatientChart, words: list[str]) -> None:
    """Fill empty given / middle / family from space-split tokens (Western ordering)."""
    if not words:
        return
    n = len(words)
    if n == 1:
        if not chart.given_name.strip():
            chart.given_name = _title_name_parts(words[0])
        return
    if n == 2:
        if not chart.given_name.strip():
            chart.given_name = _title_name_parts(words[0])
        if not chart.family_name.strip():
            chart.family_name = _title_name_parts(words[1])
        return
    if n == 3:
        if not chart.given_name.strip():
            chart.given_name = _title_name_parts(words[0])
        if not chart.middle_name.strip():
            chart.middle_name = _title_name_parts(words[1])
        if not chart.family_name.strip():
            chart.family_name = _title_name_parts(words[2])
        return
    if not chart.given_name.strip():
        chart.given_name = _title_name_parts(words[0])
    if not chart.middle_name.strip():
        chart.middle_name = _title_name_parts(words[1])
    if not chart.family_name.strip():
        chart.family_name = _title_name_parts(" ".join(words[2:]))


def _apply_leading_name_tokens(chart: PatientChart, rest: list[str]) -> None:
    """After stripping family suffix from display: first token → given, rest → middle."""
    if not rest:
        return
    if not chart.given_name.strip():
        chart.given_name = _title_name_parts(rest[0])
    if len(rest) > 1 and not chart.middle_name.strip():
        chart.middle_name = _title_name_parts(" ".join(rest[1:]))


def _infer_space_delimited_display_name(chart: PatientChart) -> None:
    """
    When patient_name is 'First ... Last' (no comma), derive structured fields.
    Handles: LLM fills only patient_name; family known but given empty; overloaded given.
    """
    raw = (chart.patient_name or "").strip()
    if not raw or "," in raw:
        return
    words = raw.split()
    if len(words) < 2:
        return
    pn_cf = raw.casefold()
    gn = (chart.given_name or "").strip()
    if gn.casefold() == pn_cf:
        chart.given_name = ""
        chart.middle_name = ""
    fn = (chart.family_name or "").strip()
    if fn:
        fam_words = fn.split()
        if _tokens_suffix_match(words, fam_words):
            rest = words[: -len(fam_words)]
            if rest and not (chart.given_name or "").strip():
                _apply_leading_name_tokens(chart, rest)
            return
    if not chart.given_name.strip() or not chart.family_name.strip():
        _infer_from_word_count(chart, words)


def _split_overloaded_given_name(chart: PatientChart) -> None:
    """If given_name has several tokens and middle is blank, treat 2+ as first + middle."""
    if chart.middle_name.strip():
        return
    g = (chart.given_name or "").strip()
    if not g or " " not in g:
        return
    parts = g.split()
    if len(parts) < 2:
        return
    chart.given_name = _title_name_parts(parts[0])
    chart.middle_name = _title_name_parts(" ".join(parts[1:]))


def _reconcile_structured_name(chart: PatientChart) -> None:
    """Display name = given + middle + family when structured fields exist."""
    g = chart.given_name.strip()
    m = chart.middle_name.strip()
    f = chart.family_name.strip()
    chunks = [x for x in (g, m, f) if x]
    if len(chunks) >= 2:
        chart.patient_name = " ".join(chunks)
    elif g and f:
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
    ocr_text = clean_clinical_ocr_text(ocr_text)

    chart = PatientChart()
    if doc_stem:
        demo, prov = _load_llm_demographics_and_providers(doc_stem)
        _apply_llm_string_fields(chart, demo, prov)

    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    lines = _merge_label_continuation_lines(lines)

    for line in lines:
        for pattern, field_name in LABEL_PATTERNS:
            if field_name not in _REGEX_FIELDS:
                continue
            val = _extract_value(line, pattern, field_name)
            if val and not getattr(chart, field_name):
                setattr(chart, field_name, val)
            # No break: one OCR line may carry several fields (e.g. C-CDA
            # "LAST, FIRST Admin Sex: Male DOB: …").

    # Fallback: slash-style DOB on a line if primary patterns missed
    if not chart.date_of_birth:
        for line in lines:
            if _skip_line_for_dob_fallback(line):
                continue
            m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", line)
            if m and not re.search(r"[A-Za-z]", m.group(1)):
                parsed = _parse_dob_flexible(m.group(1))
                if parsed:
                    chart.date_of_birth = parsed
                break

    _parse_comma_form_name(chart)
    _infer_space_delimited_display_name(chart)
    _split_overloaded_given_name(chart)
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
    and write a FHIR R4 Bundle (JSON) file.

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
        Path to the written file: ehr_ready/<doc_stem>_chart.fhir.json
    """
    if ocr_text is None:
        ocr_path = OCR_DIR / f"{doc_stem}.txt"
        if not ocr_path.exists():
            raise FileNotFoundError(f"OCR text file not found: {ocr_path}")
        ocr_text = ocr_path.read_text(encoding="utf-8")

    chart = extract_patient_demographics(ocr_text, doc_stem=doc_stem)
    chart.source_document = source_document or f"{doc_stem}.pdf"
    chart.refresh_computed_age()

    EHR_READY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ehr_fhir_output_path(doc_stem)
    bundle = build_fhir_collection_bundle(chart, doc_stem)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    return out_path


def get_patient_chart_path(doc_stem: str) -> Optional[Path]:
    """Return path to FHIR Bundle file if it exists."""
    p = ehr_fhir_output_path(doc_stem)
    return p if p.exists() else None


def _sanitize_extra_field_key(raw: str, idx: int, used: set[str]) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", (raw or "").strip())[:48]
    s = (s or f"field_{idx}").lower().strip("_")
    base = s
    n = 2
    while s in used:
        s = f"{base}_{n}"
        n += 1
    return s


def normalize_llm_additional_fields(raw: Any) -> list[dict[str, Any]]:
    """
    Turn LLM ``additional_fields`` array into stable rows for the confirm UI + FHIR.
    Each row: key, label, value, hint, fhir_resource_hint.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        key_raw = str(item.get("key") or "").strip()
        if not key_raw:
            key_raw = re.sub(r"[^a-zA-Z0-9]+", "_", label.lower()).strip("_") or ""
        key = _sanitize_extra_field_key(key_raw or f"field_{i}", i, used_keys)
        used_keys.add(key)
        hint = str(item.get("source_hint") or item.get("hint") or "").strip()
        frh = str(item.get("fhir_resource_hint") or "Observation").strip() or "Observation"
        display_label = label or key.replace("_", " ").title()
        if not display_label and not value and not hint:
            continue
        if frh.strip() == "MedicationStatement" and value:
            value = compact_medication_display_value(value)
            if not value.strip():
                continue
        out.append(
            {
                "key": key,
                "label": display_label,
                "value": value,
                "hint": hint,
                "fhir_resource_hint": frh,
            }
        )
    return out


def compact_medication_display_value(text: str) -> str:
    """
    Reduce OCR/LLM med lines to drug name + strength/dose for the review text box.

    Examples
    --------
    ``atorvastatin (atorvastatin 40 mg oral tablet)`` → ``atorvastatin 40 mg``
    Leading labels like ``Status: Ordered`` are stripped when possible.
    """
    if not (text or "").strip():
        return ""
    t = " ".join(text.split())
    t = re.sub(
        r"(?i)^(?:status|order|rx|prescription|medication|sig|frequency)\s*:\s*",
        "",
        t,
    ).strip()
    t = re.sub(r"(?i)^ordered\s+", "", t).strip()
    t = re.sub(
        r"^\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]m)?)?\s*",
        "",
        t,
        flags=re.I,
    ).strip()
    t = re.sub(r"^[\-\*•\u2022]+\s*", "", t).strip()

    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", t)
    if m:
        outer = m.group(1).strip()
        inner = m.group(2).strip()
        dose_m = re.search(
            r"\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?|iu)\b",
            inner,
            re.I,
        )
        if dose_m and outer:
            inner_rest = inner[dose_m.start() :].strip()
            inner_rest = re.sub(
                r"\s+(oral|topical|delayed|extended|release|tablet|capsule|cap|tab|"
                r"solution|suspension|powder)\b.*$",
                "",
                inner_rest,
                flags=re.I,
            ).strip()
            return f"{outer} {inner_rest}".strip()
        if len(t) > 140:
            clip = inner[:90] + ("…" if len(inner) > 90 else "")
            return f"{outer} ({clip})".strip() if inner else outer
        return t

    dose_m = re.search(r"\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?|iu)\b", t, re.I)
    if dose_m:
        before = t[: dose_m.start()].strip()
        words = before.split()
        name_like = " ".join(words[-8:]) if len(words) > 8 else before
        tail = t[dose_m.start() :]
        tail = re.sub(
            r"\s+(oral|topical|delayed|extended|release|tablet|capsule)\b.*$",
            "",
            tail,
            flags=re.I,
        ).strip()
        return f"{name_like} {tail}".strip()

    return t[:400] if len(t) > 400 else t


_OCR_EXTRA_LABEL: dict[str, str] = {
    "lab_result": "Lab / result (OCR candidate)",
    "medication": "Medication (OCR candidate)",
    "problem": "Problem / history (OCR candidate)",
    "order": "Order (OCR candidate)",
}

_OCR_EXTRA_FHIR: dict[str, str] = {
    "lab_result": "Observation",
    "medication": "MedicationStatement",
    "problem": "Condition",
    "order": "ServiceRequest",
}


def synthesize_review_extra_fields_from_ocr(
    doc_stem: str, *, max_rows: int = 60, min_chars: int = 10
) -> list[dict[str, Any]]:
    """
    When the LLM omits ``additional_fields``, build review rows from the same
    OCR + keyword / spaCy path used elsewhere so long summaries are not blank.
    """
    from nlp_local import extract_candidates_from_text

    path = OCR_DIR / f"{doc_stem}.txt"
    if not path.exists():
        return []
    body = clean_clinical_ocr_text(path.read_text(encoding="utf-8"))
    candidates = extract_candidates_from_text(body)
    used_keys: set[str] = set()
    seen_norm: set[str] = set()
    out: list[dict[str, Any]] = []

    def push(category: str, text: str, nlp_tags: str = "") -> None:
        if len(out) >= max_rows:
            return
        t = " ".join(text.split())
        if len(t) < min_chars:
            return
        if category in ("demographic", "other"):
            return
        low = t.lower()
        if low in ("--", "no data to display", "n/a") or low.startswith(
            ("[tesseract", "[easyocr")
        ):
            return
        if re.match(r"(?i)last\s+modified\s*:", t):
            return
        if re.search(r"(?i)no data available", low):
            return
        if category == "medication":
            if low in ("medication", "medications", "current medications"):
                return
            t = compact_medication_display_value(t)
            if len(t) < 3:
                return
        norm = low[:400]
        if category == "medication":
            norm = t.lower()[:400]
        if norm in seen_norm:
            return
        seen_norm.add(norm)
        idx = len(out)
        key = _sanitize_extra_field_key(f"ocr_{category}_{idx}", idx, used_keys)
        used_keys.add(key)
        label = _OCR_EXTRA_LABEL.get(category, "Clinical text (OCR candidate)")
        hint = "Rule-based / NLP scan of OCR (use if clinically relevant)."
        if nlp_tags.strip():
            hint = f"{hint} Entities: {nlp_tags.strip()}"
        frh = _OCR_EXTRA_FHIR.get(category, "Observation")
        out.append(
            {
                "key": key,
                "label": label,
                "value": t[:4000],
                "hint": hint,
                "fhir_resource_hint": frh,
            }
        )

    for cleaned, category, _conf, nlp_tags in candidates:
        push(category, cleaned, nlp_tags)

    if not out:
        for line in body.splitlines():
            ln = line.strip()
            if len(ln) < min_chars:
                continue
            low = ln.lower()
            if low.startswith(("[tesseract", "[easyocr")):
                continue
            cat = guess_category_from_keywords(low)
            push(cat, ln, "")

    return out


def load_llm_review_extensions(doc_stem: str) -> tuple[str, str, list[dict[str, Any]]]:
    """
    Read ``review_data/<stem>_llm.json`` for document type + dynamic fields.

    If the LLM file is missing, unreadable, or ``additional_fields`` is empty after
    normalization, fills review rows via :func:`synthesize_review_extra_fields_from_ocr`
    so medications, section cues, vitals, etc. still appear for confirmation.

    Returns
    -------
    document_type_code, document_type_display, additional_fields
    """
    path = REVIEW_DATA_DIR / f"{doc_stem}_llm.json"
    if not path.exists():
        return "", "", synthesize_review_extra_fields_from_ocr(doc_stem)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", synthesize_review_extra_fields_from_ocr(doc_stem)
    if isinstance(data, list):
        return "", "", synthesize_review_extra_fields_from_ocr(doc_stem)
    doc_type = data.get("document_type")
    code, display = "", ""
    if isinstance(doc_type, dict):
        code = str(doc_type.get("code") or "").strip()
        display = str(doc_type.get("display") or "").strip()
    elif isinstance(doc_type, str) and doc_type.strip():
        display = doc_type.strip()
        code = _sanitize_extra_field_key(
            re.sub(r"[^a-zA-Z0-9]+", "_", display.lower()).strip("_"),
            0,
            set(),
        )
    fields = normalize_llm_additional_fields(data.get("additional_fields"))
    if not fields:
        fields = synthesize_review_extra_fields_from_ocr(doc_stem)
    return code, display, fields


# Attribute keys used for confirmation form and writing from user-confirmed values.
CHART_ATTRS = (
    "patient_name",
    "given_name",
    "middle_name",
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
    *,
    address_structured: Optional[dict[str, Any]] = None,
    document_type_code: str = "",
    document_type_display: str = "",
    confirmed_extra_fields: Optional[list[dict[str, Any]]] = None,
    **kwargs: Any,
) -> Tuple[Path, str]:
    """
    Build and write a FHIR R4 Bundle (JSON) from user-confirmed (and optionally
    edited) attribute values. Keys should match CHART_ATTRS.

    When given name, family name, and a parseable DOB yield a
    ``stem_from_patient_demographics`` stem, PDF / OCR / LLM / prior FHIR files
    are renamed from ``doc_stem`` to that stem (same rules as post-OCR rename)
    so on-disk names match the confirmed patient.

    ``address_structured`` is optional FHIR-style address from the LLM (or edited
    client JSON); when present, Patient.address uses ``line`` / ``city`` / etc.

    ``confirmed_extra_fields`` are optional LLM-derived rows confirmed on the same form.

    Returns
    -------
    Tuple[Path, str]
        Path to the written FHIR JSON, and the PDF basename after any rename
        (e.g. ``First_Last_MM-DD-YYYY.pdf``).
    """
    def _s(key: str) -> str:
        v = kwargs.get(key, "")
        return str(v).strip() if v is not None else ""

    chart = PatientChart(
        patient_name=_s("patient_name"),
        given_name=_s("given_name"),
        middle_name=_s("middle_name"),
        family_name=_s("family_name"),
        date_of_birth=_s("date_of_birth"),
        sex=_s("sex"),
        mrn=_s("mrn"),
        age=_s("age"),
        address=_s("address"),
        phone=_s("phone"),
        ordering_provider=_s("ordering_provider"),
        referring_provider=_s("referring_provider"),
        lab_facility=_s("lab_facility"),
        source_document=source_document,
        address_structured=address_structured,
    )
    chart.refresh_computed_age()

    out_stem = doc_stem
    desired = stem_from_patient_demographics(chart)
    if desired:
        final_stem = _pick_unique_stem(desired, doc_stem)
        if final_stem != doc_stem:
            try:
                rename_pipeline_artifacts(doc_stem, final_stem)
            except OSError as exc:
                LOGGER.exception("Rename after confirm failed: %s -> %s", doc_stem, final_stem)
                raise RuntimeError(
                    f"Could not rename files to match confirmed name: {exc}"
                ) from exc
            out_stem = final_stem
            chart.source_document = f"{final_stem}.pdf"
            LOGGER.info(
                "Renamed pipeline artifacts after confirm: %s -> %s", doc_stem, final_stem
            )

    EHR_READY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ehr_fhir_output_path(out_stem)
    bundle = build_fhir_collection_bundle(
        chart,
        out_stem,
        document_type_code=document_type_code,
        document_type_display=document_type_display,
        confirmed_extra_fields=confirmed_extra_fields,
    )
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    return out_path, f"{out_stem}.pdf"


def _segment_for_filename(name: str) -> str:
    """ASCII-ish token for path segment (no spaces)."""
    if not name or not name.strip():
        return ""
    s = re.sub(r"[^A-Za-z0-9\-]", "", name.strip().replace(" ", ""))
    return s[:48] if s else ""


def _dob_mmddyyyy_chart(chart: PatientChart) -> Optional[str]:
    raw = (chart.date_of_birth or "").strip()
    if not raw:
        return None
    # Match chart parsing: ISO/slash via _normalize_fhir_date; prose DOB via flexible parser.
    iso = _normalize_fhir_date(raw) or _parse_dob_flexible(raw)
    if not iso:
        return None
    y, m, d = iso.split("-")
    return f"{m.zfill(2)}-{d.zfill(2)}-{y}"


def _given_family_for_filename(chart: PatientChart) -> tuple[str, str]:
    g_full = (chart.given_name or "").strip()
    g = g_full.split()[0] if g_full else ""
    f = (chart.family_name or "").strip()
    if g and f:
        return g, f
    pn = (chart.patient_name or "").strip()
    if not pn:
        return "", ""
    if "," in pn:
        parts = [p.strip() for p in pn.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[1], parts[0]
    words = pn.split()
    if len(words) == 2:
        return words[0], words[1]
    if len(words) == 3:
        return words[0], words[2]
    if len(words) >= 4:
        return words[0], " ".join(words[2:])
    return "", ""


def stem_from_patient_demographics(chart: PatientChart) -> Optional[str]:
    """
    Build ``First_Last_MM-DD-YYYY`` file stem from chart fields.
    Returns None if name or DOB cannot be derived reliably.
    """
    first, last = _given_family_for_filename(chart)
    seg_f = _segment_for_filename(first)
    seg_l = _segment_for_filename(last)
    dob = _dob_mmddyyyy_chart(chart)
    if not seg_f or not seg_l or not dob:
        return None
    return f"{seg_f}_{seg_l}_{dob}"


def _pick_unique_stem(desired_base: str, old_stem: str) -> str:
    """Pick ``First_Last_DOB`` or ``First_Last_DOB_2`` if the PDF name is already taken."""
    if desired_base == old_stem:
        return old_stem
    candidate = desired_base
    suffix = 2
    while (PROCESSED_SCANS_DIR / f"{candidate}.pdf").exists():
        candidate = f"{desired_base}_{suffix}"
        suffix += 1
    return candidate


def rename_pipeline_artifacts(old_stem: str, new_stem: str) -> None:
    """Rename PDF, OCR txt, LLM JSON, optional FHIR + confirmation logs."""
    if old_stem == new_stem:
        return
    pairs: list[tuple[Path, Path]] = [
        (PROCESSED_SCANS_DIR / f"{old_stem}.pdf", PROCESSED_SCANS_DIR / f"{new_stem}.pdf"),
        (OCR_DIR / f"{old_stem}.txt", OCR_DIR / f"{new_stem}.txt"),
        (REVIEW_DATA_DIR / f"{old_stem}_llm.json", REVIEW_DATA_DIR / f"{new_stem}_llm.json"),
        (EHR_READY_DIR / f"{old_stem}_chart.fhir.json", EHR_READY_DIR / f"{new_stem}_chart.fhir.json"),
        (REVIEW_LOGS_DIR / f"{old_stem}_confirmations.json", REVIEW_LOGS_DIR / f"{new_stem}_confirmations.json"),
    ]
    for src, dst in pairs:
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.resolve() != src.resolve():
            dst.unlink()
        src.rename(dst)
        LOGGER.info("Renamed artifact: %s -> %s", src.name, dst.name)


def maybe_rename_after_pipeline(pdf_path: Path, ocr_full_text: str) -> Path:
    """
    After OCR + LLM, rename ``processed_scans/<old>.pdf`` and related files to
    ``First_Last_MM-DD-YYYY`` when demographics are available.

    Set ``SKIP_PATIENT_FILENAME_RENAME=1`` to keep the original upload name.
    """
    if os.environ.get("SKIP_PATIENT_FILENAME_RENAME", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return pdf_path.resolve()

    pdf_path = pdf_path.resolve()
    old_stem = pdf_path.stem
    chart = extract_patient_demographics(ocr_full_text, doc_stem=old_stem)
    base = stem_from_patient_demographics(chart)
    if not base:
        LOGGER.info(
            "Patient-based filename skipped (need given+family name and parseable DOB)."
        )
        return pdf_path

    new_stem = _pick_unique_stem(base, old_stem)
    if new_stem == old_stem:
        return pdf_path

    try:
        rename_pipeline_artifacts(old_stem, new_stem)
    except OSError as exc:
        LOGGER.warning("Could not rename pipeline files to %s: %s", new_stem, exc)
        return pdf_path

    new_pdf = PROCESSED_SCANS_DIR / f"{new_stem}.pdf"
    LOGGER.info("Renamed upload to patient-based name: %s -> %s", old_stem, new_stem)
    return new_pdf


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build FHIR Bundle (patient chart) from OCR text."
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
