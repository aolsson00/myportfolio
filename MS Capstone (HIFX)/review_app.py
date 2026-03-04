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

import pathlib

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    flash,
)

from extraction_engine import load_extracted_items, save_confirmations


BASE_DIR = pathlib.Path(".").resolve()
PROCESSED_DIR = BASE_DIR / "processed_scans"

app = Flask(__name__)
app.secret_key = "capstone-local-only"  # safe enough for local use


def _list_documents() -> list[str]:
    """
    List available PDF documents from the processed_scans directory.
    """
    if not PROCESSED_DIR.exists():
        return []
    return sorted(
        [p.name for p in PROCESSED_DIR.glob("*.pdf")]
        + [p.name for p in PROCESSED_DIR.glob("*.PDF")]
    )


@app.route("/")
def index():
    """
    Show a simple list of processed PDFs that can be reviewed.
    """
    docs = _list_documents()
    return render_template("index.html", documents=docs)


@app.route("/pdf/<path:filename>")
def pdf(filename: str):
    """
    Serve a processed PDF for embedding in the browser.
    """
    return send_from_directory(PROCESSED_DIR, filename)


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
    )


if __name__ == "__main__":  # pragma: no cover
    # Run the app locally: python review_app.py
    app.run(debug=True)

