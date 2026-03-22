"""
review_app.py
--------------

Minimal, fully local web app to review extracted clinical data.

Design goals:
- 100% local, no external services.
- Very simple UI: left pane shows the PDF, right pane lists extracted items.
- User manually reviews items and confirms they are correct.
"""

from __future__ import annotations

import os
import pathlib
import shutil
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    flash,
)
from werkzeug.utils import secure_filename

from extraction_engine import load_extracted_items, save_confirmations
from ocr_engine import extract_text_from_pdf
from patient_chart import (
    CHART_ATTRS,
    extract_patient_demographics,
    get_patient_chart_path,
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
    EHR-ready JSON files with file creation date. Newest first.
    """
    if not EHR_READY_DIR.exists():
        return []
    rows: list[tuple[pathlib.Path, float]] = []
    for p in EHR_READY_DIR.iterdir():
        if p.is_file() and p.suffix.lower() == ".json":
            rows.append((p, p.stat().st_mtime))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [
        {"name": p.name, "date": _file_display_date(p)}
        for p, _ in rows
    ]


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[-1].lower() == "pdf"


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
        doc_stem = pathlib.Path(filename).stem
        handwriting_assist = request.form.get("handwriting_assist") == "on"
        # Run OCR (optional EasyOCR alongside Tesseract for handwriting)
        try:
            _full_text, _txt_path, llm_ok = extract_text_from_pdf(
                str(processed_path), handwriting_assist=handwriting_assist
            )
        except Exception as e:
            flash(f"Uploaded and moved, but OCR failed: {e}", "error")
            return redirect(url_for("index"))
        # Redirect to confirm demographics before writing EHR-ready file
        flash(f"OCR complete for {filename}. Please confirm each attribute below.", "success")
        if llm_ok is True:
            flash(
                "LLM extraction finished — structured items are available on Review.",
                "success",
            )
        elif llm_ok is False:
            flash(
                "LLM extraction could not run (is Ollama running? Model installed?). "
                "Review will use rule-based lines from OCR text.",
                "warning",
            )
        return redirect(url_for("confirm_chart", filename=filename))
    return render_template("upload.html")


@app.route("/confirm_chart/<path:filename>", methods=["GET", "POST"])
def confirm_chart(filename: str):
    """
    After upload + OCR: show extracted demographics and require the user to
    confirm each attribute. The EHR-ready JSON is written only when all are
    confirmed (user can edit values before confirming).
    """
    doc_stem = pathlib.Path(filename).stem
    ocr_path = BASE_DIR / "temp_extractions" / f"{doc_stem}.txt"
    if not ocr_path.exists():
        flash(f"OCR text not found for {filename}. Run OCR first.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        # Require all confirm_* checkboxes to be checked
        confirms = {attr: request.form.get(f"confirm_{attr}") == "on" for attr in CHART_ATTRS}
        if not all(confirms.values()):
            flash("Please confirm every attribute before writing the EHR-ready file.", "error")
            values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
            return render_template(
                "confirm_chart.html",
                filename=filename,
                doc_stem=doc_stem,
                chart=values,
                chart_attrs=CHART_ATTRS,
                confirmed=confirms,
                sparse_extraction=sum(1 for v in values.values() if (v or "").strip()) <= 2,
            )
        # Build chart from form values (user may have edited) and write JSON
        values = {attr: request.form.get(attr, "") for attr in CHART_ATTRS}
        try:
            write_patient_chart_from_values(
                doc_stem, source_document=filename, **values
            )
        except Exception as e:
            flash(f"Could not write EHR-ready file: {e}", "error")
            return render_template(
                "confirm_chart.html",
                filename=filename,
                doc_stem=doc_stem,
                chart=values,
                chart_attrs=CHART_ATTRS,
                confirmed=confirms,
                sparse_extraction=sum(1 for v in values.values() if (v or "").strip()) <= 2,
            )
        flash("EHR-ready file saved. You can review the document or download the chart.", "success")
        return redirect(url_for("index"))

    # GET: extract demographics from OCR and show form
    text = ocr_path.read_text(encoding="utf-8")
    chart_obj = extract_patient_demographics(text, doc_stem=doc_stem)
    chart = {attr: getattr(chart_obj, attr, "") for attr in CHART_ATTRS}
    nonempty = sum(1 for v in chart.values() if (v or "").strip())
    sparse_extraction = nonempty <= 2
    return render_template(
        "confirm_chart.html",
        filename=filename,
        doc_stem=doc_stem,
        chart=chart,
        chart_attrs=CHART_ATTRS,
        confirmed={attr: False for attr in CHART_ATTRS},
        sparse_extraction=sparse_extraction,
    )


@app.route("/")
def index():
    """
    Show uploaded/processed PDFs and EHR-ready files (with dates).
    """
    document_entries = _list_processed_pdf_entries()
    ehr_ready_entries = _list_ehr_ready_entries()
    return render_template(
        "index.html",
        document_entries=document_entries,
        ehr_ready_entries=ehr_ready_entries,
    )


@app.route("/pdf/<path:filename>")
def pdf(filename: str):
    """
    Serve a processed PDF for embedding in the browser.
    """
    return send_from_directory(PROCESSED_DIR, filename)


@app.route("/ehr_chart/<path:filename>")
def ehr_chart(filename: str):
    """
    Serve an EHR-ready patient chart JSON (for download).
    """
    return send_from_directory(
        EHR_READY_DIR, filename, as_attachment=True, download_name=filename
    )


@app.route("/review/<path:filename>", methods=["GET", "POST"])
def review(filename: str):
    """
    Two-pane review page:
    - Left: embedded PDF.
    - Right: list of extracted lines with confirmation checkboxes.
    """
    pdf_name = filename
    doc_path = PROCESSED_DIR / pdf_name
    if not doc_path.exists():
        flash(f"PDF not found: {pdf_name}", "error")
        return redirect(url_for("index"))

    doc_stem = pathlib.Path(pdf_name).stem  # "Labs" from "Labs.PDF"
    llm_json_path = BASE_DIR / "review_data" / f"{doc_stem}_llm.json"
    using_llm = llm_json_path.exists()
    patient_chart_path = get_patient_chart_path(doc_stem)

    if request.method == "POST":
        # Collect confirmations from form data
        confirmations = {}
        for key, value in request.form.items():
            if key.startswith("confirm_"):
                item_id = key.split("confirm_")[-1]
                confirmations[item_id] = value == "on"

        if not confirmations:
            flash(
                "No items were confirmed. Please review the extracted data points and "
                "check at least one box before saving.",
                "error",
            )
            return redirect(url_for("review", filename=pdf_name))

        save_confirmations(doc_stem, confirmations)
        flash("Confirmations saved. AI suggestions have been reviewed.", "success")
        return redirect(url_for("review", filename=pdf_name))

    # GET: load extracted items for this document
    try:
        items = load_extracted_items(doc_stem)
    except FileNotFoundError:
        flash(
            f"OCR text file not found for {pdf_name}. "
            f"Expected temp_extractions/{doc_stem}.txt",
            "error",
        )
        return redirect(url_for("index"))

    return render_template(
        "review.html",
        pdf_name=pdf_name,
        doc_stem=doc_stem,
        items=items,
        using_llm=using_llm,
        patient_chart_filename=patient_chart_path.name if patient_chart_path else None,
    )


if __name__ == "__main__":  # pragma: no cover
    # Default port 5001: macOS often reserves 5000 for AirPlay Receiver.
    # Override with: PORT=5000 python review_app.py
    port = int(os.environ.get("PORT", "5001"))
    app.run(debug=True, host="127.0.0.1", port=port)

