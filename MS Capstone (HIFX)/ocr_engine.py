"""
ocr_engine.py
---------------

Module responsible for performing OCR on scanned clinical PDFs.

Key responsibilities:
- Convert multi-page PDFs to images.
- Apply image preprocessing (denoising and binarization) using OpenCV.
- Run Tesseract (via pytesseract) on each page.
- Return the full extracted text and persist it to a temporary text file.

This implementation is designed for an academic capstone project focused on
legacy data migration for OpenEMR and emphasizes clarity, testability, and
robust logging.
"""

import logging
import pathlib
from typing import Tuple

import cv2
import pytesseract
from pdf2image import convert_from_path


LOGGER = logging.getLogger(__name__)


def _ensure_output_dir() -> pathlib.Path:
    """
    Ensure that the ./temp_extractions directory exists.

    Returns
    -------
    pathlib.Path
        The absolute path to the `temp_extractions` directory located at the
        project root (i.e., relative to the process working directory).
    """
    base_path = pathlib.Path(".").resolve()
    output_dir = base_path / "temp_extractions"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def preprocess_image(pil_image) -> "cv2.Mat":
    """
    Preprocess a PDF page image for OCR, following Li et al. (2024).

    Steps:
    1. Convert PIL image (RGB) to OpenCV BGR format.
    2. Convert to grayscale.
    3. Apply Gaussian blur for denoising.
    4. Apply Otsu's thresholding for binarization.

    Parameters
    ----------
    pil_image : PIL.Image.Image
        A single page image as returned by `pdf2image.convert_from_path`.

    Returns
    -------
    cv2.Mat
        A binarized image suitable for Tesseract OCR.
    """
    import numpy as np  # Local import to keep the global namespace minimal

    # 1) PIL (RGB) → NumPy array → OpenCV BGR
    rgb = pil_image.convert("RGB")
    bgr = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)

    # 2) Grayscale conversion
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # 3) Denoising: Gaussian blur to smooth scanner noise
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # 4) Binarization: Otsu's thresholding (crucial for faxed/low‑quality scans)
    _, binary = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    return binary


def extract_text_from_pdf(file_path: str) -> Tuple[str, str]:
    """
    Extract text from a multi-page PDF using Tesseract OCR.

    The function performs the following steps:
    1. Convert each page of the PDF to an image using `pdf2image`.
    2. Apply OpenCV-based preprocessing (denoising and binarization).
    3. Run Tesseract OCR for each page and concatenate results.
    4. Persist the concatenated text to a temporary `.txt` file inside
       `temp_extractions/`.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the PDF file to be processed.

    Returns
    -------
    Tuple[str, str]
        A tuple containing:
        - full_text: The concatenated OCR output from all pages.
        - output_txt_path: Absolute path to the temporary text file that
          stores the OCR output.
    """
    pdf_path = pathlib.Path(file_path).resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    LOGGER.info("Starting OCR extraction for PDF: %s", pdf_path)

    # Convert PDF pages to images at 300 DPI, as recommended for OCR quality
    try:
        pages = convert_from_path(str(pdf_path), dpi=300)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to convert PDF to images: %s", exc)
        raise

    LOGGER.info("PDF contains %d page(s) for OCR processing.", len(pages))

    ocr_results = []
    for idx, page in enumerate(pages, start=1):
        LOGGER.debug("Processing page %d", idx)

        # Apply Li et al. (2024)-inspired preprocessing
        preprocessed = preprocess_image(page)

        # Tesseract configuration:
        # - OEM 3: Default LSTM-based engine
        # - PSM 6: Assume a block of text
        config = "--oem 3 --psm 6"

        try:
            text = pytesseract.image_to_string(preprocessed, config=config)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Tesseract OCR failed on page %d: %s", idx, exc)
            raise

        LOGGER.debug("Completed OCR for page %d (chars=%d)", idx, len(text))
        ocr_results.append(text)

    full_text = "\n\n".join(ocr_results)
    LOGGER.info(
        "Completed OCR for %s (total_chars=%d)",
        pdf_path.name,
        len(full_text),
    )

    # Persist to ./temp_extractions/{filename}.txt
    output_dir = _ensure_output_dir()
    output_filename = f"{pdf_path.stem}.txt"
    output_path = output_dir / output_filename

    with output_path.open("w", encoding="utf-8") as f:
        f.write(full_text)

    LOGGER.info("OCR output written to: %s", output_path)

    return full_text, str(output_path)


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run OCR on a scanned clinical PDF."
    )
    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to the input PDF file.",
    )

    args = parser.parse_args()
    text, txt_path = extract_text_from_pdf(args.pdf_path)
    LOGGER.info("Extraction complete. Output text file: %s", txt_path)
    print(txt_path)

