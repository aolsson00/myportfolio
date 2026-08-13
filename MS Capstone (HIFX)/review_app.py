"""
review_app.py
--------------

Minimal, fully local web app: upload PDFs, confirm demographics, export FHIR.

Design goals:
- 100% local, no external services.
- Upload → OCR → confirm chart → write FHIR Bundle under ehr_ready/.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import threading
from datetime import datetime
from typing import Any, Optional

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    flash,
)
from werkzeug.utils import secure_filename

import job_progress
from ocr_engine import extract_text_from_pdf
from text_cleaning import clean_clinical_ocr_text
from patient_chart import (
    CHART_ATTRS,
    extract_patient_demographics,
    load_llm_review_extensions,
    merge_computed_age_into_chart_fields,
    normalize_fhir_address_dict,
    write_patient_chart_from_values,
)


BASE_DIR = pathlib.Path(".").resolve()
PROCESSED_DIR = BASE_DIR / "processed_scans"
EHR_READY_DIR = BASE_DIR / "ehr_ready"
UPLOAD_DIR = BASE_DIR / "incoming_scans"

app = Flask(__name__)
app.secret_key = "capstone-local-only"  # safe enough for local use


def _file_display_date(path: pathlib.Path) -> str:
    """
    Format a human-readable date for a file. Prefer creation time (macOS
    st_birthtime); otherwise use last modification time.
    """
    st = path.stat()
    ts = getattr(st, "st_birthtime", None)
    if ts is None:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _list_processed_pdf_entries() -> list[dict[str, str]]:
    """
    PDFs in processed_scans with upload/add date (best available from filesystem).
    Newest first.
    """
    if not PROCESSED_DIR.exists():
        return []
    rows: list[tuple[pathlib.Path, float]] = []
    for p in PROCESSED_DIR.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf":
            rows.append((p, p.stat().st_mtime))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [
        {"name": p.name, "date": _file_display_date(p)}
        for p, _ in rows
    ]


def _list_ehr_ready_entries() -> list[dict[str, str]]:
    """
    FHIR Bundle files (*_chart.fhir.json) with file creation date. Newest first.
    """
    if not EHR_READY_DIR.exists():
        return []
    rows: list[tuple[pathlib.Path, float]] = []
    for p in EHR_READY_DIR.iterdir():
        if p.is_file() and p.name.endswith(".fhir.json"):
            rows.append((p, p.stat().st_mtime))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [
        {"name": p.name, "date": _file_display_date(p)}
        for p, _ in rows
    ]


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[-1].lower() == "pdf"


def _address_structured_from_form() -> Optional[dict[str, Any]]:
    """Parse hidden ``address_structured_json`` from confirm form (LLM / round-trip)."""
    raw = request.form.get("address_structured_json", "").strip()
    if not raw or raw == "{}":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return normalize_fhir_address_dict(data)


def _merge_extra_specs_with_form(
    specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for s in specs:
        k = s["key"]
        row = dict(s)
        row["value"] = request.form.get(f"extra_{k}", s.get("value", ""))
        row["label"] = request.form.get(f"extra_label_{k}", s.get("label", ""))
        row["exclude_from_fhir"] = (
            request.form.get(f"extra_exclude_fhir_{k}") == "on"
        )
        merged.append(row)
    return merged


def _confirmed_extra_rows_from_form(
    specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rows to embed in FHIR: non-excluded, non-blank value only."""
    rows: list[dict[str, Any]] = []
    for spec in specs:
        key = spec["key"]
        if request.form.get(f"extra_exclude_fhir_{key}") == "on":
            continue
        value = request.form.get(f"extra_{key}", "").strip()
        if not value:
            continue
        rows.append(
            {
                "key": key,
                "label": (
                    request.form.get(f"extra_label_{key}", "").strip()
                    or spec.get("label", "")
                ),
                "value": value,
                "fhir_resource_hint": spec.get("fhir_resource_hint", "Observation"),
            }
        )
    return rows


def _core_confirms_complete() -> bool:
    return all(request.form.get(f"confirm_{a}") == "on" for a in CHART_ATTRS)


def _extra_confirms_complete(specs: list[dict[str, Any]]) -> bool:
    if not specs:
        return True
    return all(request.form.get(f"confirm_extra_{s['key']}") == "on" for s in specs)


def _sparse_extraction_flag(
    chart: dict[str, str], extra_specs: list[dict[str, Any]]
) -> bool:
    n = sum(1 for v in chart.values() if (v or "").strip())
    n += sum(1 for s in extra_specs if (s.get("value") or "").strip())
    return n <= 2


def _run_upload_job(
    app: Flask,
    job_id: str,
    processed_path: pathlib.Path,
    filename: str,
    handwriting_assist: bool,
) -> None:
    """Background: OCR + LLM after file is already in processed_scans."""
    with app.app_context():
        try:
            job_progress.update(job_id, "start", 3, "Starting pipeline…")

            def progress_cb(step: str, pct: int, msg: str) -> None:
                job_progress.update(job_id, step, pct, msg)

            _text, _txt, _llm_ok, final_pdf = extract_text_from_pdf(
                str(processed_path),
                handwriting_assist=handwriting_assist,
                progress=progress_cb,
            )
            final_name = pathlib.Path(final_pdf).name
            # url_for needs a request context unless SERVER_NAME is set; workers have neither.
            with app.test_request_context():
                redir = url_for("confirm_chart", filename=final_name)
            job_progress.complete(job_id, redirect=redir)
        except Exception as exc:  # pragma: no cover - runtime
            job_progress.fail(job_id, str(exc))


def _run_fhir_job(
    app: Flask,
    job_id: str,
    doc_stem: str,
    filename: str,
    values: dict[str, Any],
    address_structured: Optional[dict[str, Any]],
    document_type_code: str = "",
    document_type_display: str = "",
    confirmed_extra_fields: Optional[list[dict[str, Any]]] = None,
) -> None:
    with app.app_context():
        try:
            job_progress.update(job_id, "validate", 12, "Validating confirmations…")
            job_progress.update(job_id, "fhir", 35, "Building FHIR R4 Bundle…")
            _fhir_path, pdf_name = write_patient_chart_from_values(
                doc_stem,
                source_document=filename,
                address_structured=address_structured,
                document_type_code=document_type_code,
                document_type_display=document_type_display,
                confirmed_extra_fields=confirmed_extra_fields or [],
                **values,
            )
            msg = (
                f"Saved FHIR; renamed PDF and related files to {pdf_name}."
                if pdf_name != filename
                else "Saved FHIR file."
            )
            job_progress.update(job_id, "write", 92, msg)
            with app.test_request_context():
                redir = url_for("index", fhir_saved=1)
            job_progress.complete(job_id, redirect=redir)
        except Exception as exc:  # pragma: no cover
            job_progress.fail(job_id, str(exc))


@app.route("/api/upload_pipeline", methods=["POST"])
def api_upload_pipeline():
    """
    Save PDF, then run OCR + LLM in a background thread.
    Returns JSON ``{ "job_id": "..." }`` for progress polling.
    """
    file = request.files.get("pdf")
    if not file or file.filename == "":
        return jsonify({"error": "No file"}), 400
    if not _allowed_file(file.filename):
        return jsonify({"error": "Only PDF files are allowed"}), 400

    filename = secure_filename(file.filename)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    incoming_path = UPLOAD_DIR / filename
    file.save(str(incoming_path))
    processed_path = PROCESSED_DIR / filename
    try:
        shutil.move(str(incoming_path), str(processed_path))
    except Exception as exc:
        return jsonify({"error": f"Could not move file: {exc}"}), 500

    handwriting_assist = request.form.get("handwriting_assist") == "on"
    job_id = job_progress.create_job()
    threading.Thread(
        target=_run_upload_job,
        args=(app, job_id, processed_path, filename, handwriting_assist),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def api_job_status(job_id: str):
    data = job_progress.get_job(job_id)
    if not data:
        return jsonify({"status": "unknown", "error": "Job not found"}), 404
    return jsonify(data)


@app.route("/api/confirm_fhir", methods=["POST"])
def api_confirm_fhir():
    """Async FHIR write after user confirms all fields."""
    filename = request.form.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "Missing filename"}), 400
    doc_stem = pathlib.Path(filename).stem

    if not _core_confirms_complete():
        return jsonify({"error": "Confirm every core attribute before saving"}), 400

    doc_type_code_disk, doc_type_display_disk, extra_specs = load_llm_review_extensions(
        doc_stem
    )
    if not _extra_confirms_complete(extra_specs):
        return jsonify({"error": "Confirm every additional extracted field row"}), 400

    values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
    addr_struct = _address_structured_from_form()
    doc_type_code = (
        request.form.get("document_type_code") or doc_type_code_disk or ""
    ).strip()
    document_type_display = (
        request.form.get("document_type_display") or doc_type_display_disk or ""
    ).strip()
    confirmed_extra_fields = _confirmed_extra_rows_from_form(extra_specs)

    job_id = job_progress.create_job()
    threading.Thread(
        target=_run_fhir_job,
        args=(
            app,
            job_id,
            doc_stem,
            filename,
            values,
            addr_struct,
            doc_type_code,
            document_type_display,
            confirmed_extra_fields,
        ),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """
    Import a PDF from the browser, then run the pipeline immediately:
    move to processed_scans, run OCR, build patient chart.
    """
    if request.method == "POST":
        file = request.files.get("pdf")
        if not file or file.filename == "":
            flash("Please select a PDF file.", "error")
            return redirect(url_for("upload"))
        if not _allowed_file(file.filename):
            flash("Only PDF files are allowed.", "error")
            return redirect(url_for("upload"))
        filename = secure_filename(file.filename)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        # Save to incoming_scans first, then move to processed_scans and run pipeline
        incoming_path = UPLOAD_DIR / filename
        file.save(str(incoming_path))
        processed_path = PROCESSED_DIR / filename
        try:
            shutil.move(str(incoming_path), str(processed_path))
        except Exception as e:
            flash(f"Could not move file to processed_scans: {e}", "error")
            return redirect(url_for("upload"))
        handwriting_assist = request.form.get("handwriting_assist") == "on"
        # Run OCR (optional EasyOCR alongside Tesseract for handwriting)
        try:
            _full_text, _txt_path, llm_ok, final_pdf = extract_text_from_pdf(
                str(processed_path), handwriting_assist=handwriting_assist
            )
        except Exception as e:
            flash(f"Uploaded and moved, but OCR failed: {e}", "error")
            return redirect(url_for("index"))
        final_name = pathlib.Path(final_pdf).name
        # Redirect to confirm demographics before writing FHIR Bundle
        flash(f"OCR complete for {final_name}. Please confirm each attribute below.", "success")
        if llm_ok is True:
            flash(
                "LLM extraction finished — structured demographics use LLM output when confirming.",
                "success",
            )
        elif llm_ok is False:
            flash(
                "LLM extraction could not run (is Ollama running? Model installed?). "
                "Demographics on the confirm screen will use rule-based parsing from OCR text.",
                "warning",
            )
        return redirect(url_for("confirm_chart", filename=final_name))
    return render_template("upload.html")


@app.route("/confirm_chart/<path:filename>", methods=["GET", "POST"])
def confirm_chart(filename: str):
    """
    After upload + OCR: show extracted demographics and require the user to
    confirm each attribute. The FHIR Bundle is written only when all are
    confirmed (user can edit values before confirming).
    """
    doc_stem = pathlib.Path(filename).stem
    ocr_path = BASE_DIR / "temp_extractions" / f"{doc_stem}.txt"
    if not ocr_path.exists():
        flash(f"OCR text not found for {filename}. Run OCR first.", "error")
        return redirect(url_for("index"))

    doc_type_code_disk, doc_type_display_disk, extra_specs = load_llm_review_extensions(
        doc_stem
    )

    if request.method == "POST":
        confirms = {attr: request.form.get(f"confirm_{attr}") == "on" for attr in CHART_ATTRS}
        extra_confirms = {
            s["key"]: request.form.get(f"confirm_extra_{s['key']}") == "on"
            for s in extra_specs
        }
        merged_extras = _merge_extra_specs_with_form(extra_specs)
        doc_type_code = (
            request.form.get("document_type_code") or doc_type_code_disk or ""
        ).strip()
        document_type_display = (
            request.form.get("document_type_display") or doc_type_display_disk or ""
        ).strip()

        if not all(confirms.values()):
            flash("Please confirm every core attribute before writing the FHIR Bundle.", "error")
            values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
            merge_computed_age_into_chart_fields(values)
            return render_template(
                "confirm_chart.html",
                filename=filename,
                doc_stem=doc_stem,
                chart=values,
                chart_attrs=CHART_ATTRS,
                confirmed=confirms,
                extra_fields=merged_extras,
                document_type_code=doc_type_code,
                document_type_display=document_type_display,
                extra_confirmed=extra_confirms,
                sparse_extraction=_sparse_extraction_flag(values, merged_extras),
                address_structured_json=request.form.get("address_structured_json", "{}"),
            )
        if not all(extra_confirms.values()):
            flash(
                "Please confirm every document-specific field row as well (or confirm blank values).",
                "error",
            )
            values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
            merge_computed_age_into_chart_fields(values)
            return render_template(
                "confirm_chart.html",
                filename=filename,
                doc_stem=doc_stem,
                chart=values,
                chart_attrs=CHART_ATTRS,
                confirmed=confirms,
                extra_fields=merged_extras,
                document_type_code=doc_type_code,
                document_type_display=document_type_display,
                extra_confirmed=extra_confirms,
                sparse_extraction=_sparse_extraction_flag(values, merged_extras),
                address_structured_json=request.form.get("address_structured_json", "{}"),
            )

        values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
        addr_struct = _address_structured_from_form()
        confirmed_extra_fields = _confirmed_extra_rows_from_form(extra_specs)
        try:
            _fhir_path, pdf_name = write_patient_chart_from_values(
                doc_stem,
                source_document=filename,
                address_structured=addr_struct,
                document_type_code=doc_type_code,
                document_type_display=document_type_display,
                confirmed_extra_fields=confirmed_extra_fields,
                **values,
            )
        except Exception as e:
            flash(f"Could not write FHIR Bundle: {e}", "error")
            merge_computed_age_into_chart_fields(values)
            return render_template(
                "confirm_chart.html",
                filename=filename,
                doc_stem=doc_stem,
                chart=values,
                chart_attrs=CHART_ATTRS,
                confirmed=confirms,
                extra_fields=merged_extras,
                document_type_code=doc_type_code,
                document_type_display=document_type_display,
                extra_confirmed=extra_confirms,
                sparse_extraction=_sparse_extraction_flag(values, merged_extras),
                address_structured_json=request.form.get("address_structured_json", "{}"),
            )
        if pdf_name != filename:
            flash(
                f"FHIR Bundle saved. PDF and related files were renamed to {pdf_name} "
                "to match the confirmed name. You can download the chart from the home page.",
                "success",
            )
        else:
            flash(
                "FHIR Bundle saved. You can download it from the home page.",
                "success",
            )
        return redirect(url_for("index"))

    text = clean_clinical_ocr_text(ocr_path.read_text(encoding="utf-8"))
    chart_obj = extract_patient_demographics(text, doc_stem=doc_stem)
    chart = {attr: getattr(chart_obj, attr, "") for attr in CHART_ATTRS}
    merge_computed_age_into_chart_fields(chart)
    addr_st = chart_obj.address_structured
    address_structured_json = json.dumps(addr_st, ensure_ascii=False) if addr_st else "{}"
    extra_confirmed = {s["key"]: False for s in extra_specs}
    return render_template(
        "confirm_chart.html",
        filename=filename,
        doc_stem=doc_stem,
        chart=chart,
        chart_attrs=CHART_ATTRS,
        confirmed={attr: False for attr in CHART_ATTRS},
        extra_fields=extra_specs,
        document_type_code=doc_type_code_disk,
        document_type_display=doc_type_display_disk,
        extra_confirmed=extra_confirmed,
        sparse_extraction=_sparse_extraction_flag(chart, extra_specs),
        address_structured_json=address_structured_json,
    )


@app.route("/")
def index():
    """
    Show uploaded/processed PDFs and FHIR exports (with dates).
    """
    document_entries = _list_processed_pdf_entries()
    ehr_ready_entries = _list_ehr_ready_entries()
    fhir_saved = request.args.get("fhir_saved") == "1"
    return render_template(
        "index.html",
        document_entries=document_entries,
        ehr_ready_entries=ehr_ready_entries,
        fhir_saved=fhir_saved,
    )


@app.route("/ehr_chart/<path:filename>")
def ehr_chart(filename: str):
    """
    Serve a FHIR Bundle file (JSON, application/fhir+json) for download.
    """
    return send_from_directory(
        EHR_READY_DIR,
        filename,
        as_attachment=True,
        download_name=filename,
        mimetype="application/fhir+json",
    )


if __name__ == "__main__":  # pragma: no cover
    # Default port 5001: macOS often reserves 5000 for AirPlay Receiver.
    # Override with: PORT=5000 python review_app.py
    port = int(os.environ.get("PORT", "5001"))
    app.run(debug=True, host="127.0.0.1", port=port)

