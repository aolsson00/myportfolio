"""
ocr_engine.py
---------------

Module responsible for performing OCR on scanned clinical PDFs.

Key responsibilities:
- Convert multi-page PDFs to images.
- Apply image preprocessing (denoising and binarization) using OpenCV.
- Run Tesseract (via pytesseract) on each page.
- Optionally run EasyOCR alongside Tesseract for better recovery of handwritten
  or messy text (local deep-learning model; enable with OCR_HANDWRITING_ASSIST=1).
- Return the full extracted text and persist it to a temporary text file.

This implementation is designed for an academic capstone project focused on
legacy data migration for OpenEMR and emphasizes clarity, testability, and
robust logging.
"""

import logging
import os
import pathlib
from typing import Optional, Tuple

import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_path


LOGGER = logging.getLogger(__name__)

# Single Tesseract config used for all pages (OEM 3 = LSTM, PSM 4 = single column).
TESSERACT_CONFIG = "--oem 3 --psm 4"

# Cached output dir to avoid repeated resolve() and mkdir per page.
_temp_extractions_dir: pathlib.Path | None = None

# Lazy EasyOCR reader (heavy first load; only used when handwriting assist is on)
_easyocr_reader = None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _handwriting_assist_enabled(explicit: Optional[bool]) -> bool:
    """True if we should run EasyOCR in addition to Tesseract."""
    if explicit is not None:
        return explicit
    return os.environ.get("OCR_HANDWRITING_ASSIST", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _get_easyocr_reader():
    """Load EasyOCR once (downloads models on first use)."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr  # type: ignore

        # GPU only helps when CUDA is available (uncommon on macOS).
        use_gpu = _env_bool("EASYOCR_GPU", False)
        _easyocr_reader = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
    return _easyocr_reader


def _prepare_image_for_easyocr(pil_image) -> np.ndarray:
    """
    Light preprocessing tuned for EasyOCR (deep detector + recognizer).

    EasyOCR generally prefers *non-binarized* images. We optionally upscale
    small scans and apply CLAHE to improve faint pencil / uneven lighting.
    """
    rgb = np.array(pil_image.convert("RGB"))
    h, w = rgb.shape[:2]

    # Upscale narrow pages (helps thin strokes / small handwriting).
    min_w = _env_int("EASYOCR_MIN_IMAGE_WIDTH", 1400)
    max_w = _env_int("EASYOCR_MAX_IMAGE_WIDTH", 2400)
    if w < min_w and w > 0:
        scale = min_w / float(w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        if new_w > max_w:
            scale = max_w / float(w)
            new_w = max_w
            new_h = int(h * scale)
        rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    if not _env_bool("EASYOCR_CLAHE", True):
        return rgb

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    lab = cv2.merge((l_ch, a_ch, b_ch))
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def _easyocr_text_from_image(pil_image) -> str:
    """
    Run EasyOCR on the original page image (RGB). Often better than Tesseract
    on handwritten or irregular script, at the cost of speed and CPU.

    Tunable via environment (optional):

    EASYOCR_MIN_CONFIDENCE
        Min recognizer confidence per box (0–1). Default 0.22. Higher = fewer
        garbage tokens; lower = more recall.
    EASYOCR_DECODER
        ``greedy`` (fast) or ``beamsearch`` (often slightly better, slower).
    EASYOCR_BEAM_WIDTH
        Used when decoder is beamsearch. Default 5.
    EASYOCR_PARAGRAPH
        If 1/true, merge lines into paragraphs (can read more naturally on forms).
    EASYOCR_TEXT_THRESHOLD / EASYOCR_LOW_TEXT / EASYOCR_LINK_THRESHOLD
        CRAFT detection sensitivity (see EasyOCR docs). Defaults 0.7 / 0.4 / 0.4.
    EASYOCR_MIN_SIZE
        Minimum box size in pixels (default 20). Try 10–15 if tiny writing is skipped.
    EASYOCR_CANVAS_SIZE / EASYOCR_MAG_RATIO
        Larger canvas or mag_ratio>1 can help dense pages / small handwriting.
    EASYOCR_MIN_IMAGE_WIDTH / EASYOCR_MAX_IMAGE_WIDTH / EASYOCR_CLAHE / EASYOCR_GPU
        Image prep and CUDA (Linux/NVIDIA only).
    """
    try:
        reader = _get_easyocr_reader()
        arr = _prepare_image_for_easyocr(pil_image)

        min_conf = _env_float("EASYOCR_MIN_CONFIDENCE", 0.22)
        decoder = os.environ.get("EASYOCR_DECODER", "beamsearch").strip().lower()
        if decoder not in ("greedy", "beamsearch"):
            decoder = "beamsearch"
        beam_w = _env_int("EASYOCR_BEAM_WIDTH", 5)
        paragraph = _env_bool("EASYOCR_PARAGRAPH", False)

        read_kwargs = {
            "decoder": decoder,
            "beamWidth": beam_w,
            "paragraph": paragraph,
            "detail": 1,
            # EasyOCR defaults: 0.7 / 0.4 / 0.4 — tune via env if lines are missed or noisy
            "text_threshold": _env_float("EASYOCR_TEXT_THRESHOLD", 0.7),
            "low_text": _env_float("EASYOCR_LOW_TEXT", 0.4),
            "link_threshold": _env_float("EASYOCR_LINK_THRESHOLD", 0.4),
            "min_size": _env_int("EASYOCR_MIN_SIZE", 20),
            "canvas_size": _env_int("EASYOCR_CANVAS_SIZE", 2560),
            "mag_ratio": _env_float("EASYOCR_MAG_RATIO", 1.0),
        }

        result = reader.readtext(arr, **read_kwargs)
        # result: list of (bbox, text, confidence)
        lines = []
        for _bbox, text, conf in result:
            if float(conf) >= min_conf and text and text.strip():
                lines.append(text.strip())
        return "\n".join(lines)
    except ImportError:
        LOGGER.warning(
            "EasyOCR not installed. Install with: pip install easyocr "
            "(large download). Falling back to Tesseract-only for this page."
        )
        return ""
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("EasyOCR failed on a page: %s", exc)
        return ""


def _ensure_output_dir() -> pathlib.Path:
    """
    Ensure that the ./temp_extractions directory exists.

    Returns
    -------
    pathlib.Path
        The absolute path to the `temp_extractions` directory located at the
        project root (i.e., relative to the process working directory).
    """
    global _temp_extractions_dir
    if _temp_extractions_dir is None:
        base_path = pathlib.Path(".").resolve()
        _temp_extractions_dir = base_path / "temp_extractions"
        _temp_extractions_dir.mkdir(parents=True, exist_ok=True)
    return _temp_extractions_dir


def preprocess_image(pil_image) -> "cv2.Mat":
    """
    Preprocess a PDF page image for OCR, following Li et al. (2024).

    Steps:
    1. Convert PIL image (RGB) to OpenCV BGR format.
    2. Convert to grayscale.
    3. Apply Gaussian blur for denoising.
    4. Apply adaptive thresholding (plus Otsu as fallback) for binarization.

    Parameters
    ----------
    pil_image : PIL.Image.Image
        A single page image as returned by `pdf2image.convert_from_path`.

    Returns
    -------
    cv2.Mat
        A binarized image suitable for Tesseract OCR.
    """
    # 1) PIL (RGB) → NumPy array → OpenCV BGR
    rgb = pil_image.convert("RGB")
    bgr = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)

    # 2) Grayscale conversion
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # 3) Denoising: Gaussian blur to smooth scanner noise
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # 4) Binarization:
    #    - First try adaptive thresholding, which can handle uneven lighting
    #      and some handwritten strokes better.
    #    - Otsu's global thresholding can still be useful on cleaner pages.
    try:
        adaptive = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2,
        )
        binary = adaptive
    except Exception:
        # Fallback to Otsu if adaptive thresholding fails for any reason
        _, binary = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )

    return binary


def extract_text_from_pdf(
    file_path: str,
    handwriting_assist: Optional[bool] = None,
) -> Tuple[str, str, Optional[bool]]:
    """
    Extract text from a multi-page PDF using Tesseract OCR.

    The function performs the following steps:
    1. Convert each page of the PDF to an image using `pdf2image`.
    2. Apply OpenCV-based preprocessing (denoising and binarization).
    3. Run Tesseract OCR for each page and concatenate results.
    4. If handwriting assist is enabled, also run EasyOCR on each page and
       append a clearly labeled section (better for some handwriting).
    5. Persist the concatenated text to a temporary `.txt` file inside
       `temp_extractions/`.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the PDF file to be processed.
    handwriting_assist : bool, optional
        If True, run EasyOCR in addition to Tesseract. If None, use environment
        variable ``OCR_HANDWRITING_ASSIST`` (1/true/yes/on to enable).

    Notes
    -----
    Environment variable ``OCR_PDF_DPI`` (default 300) sets pdf2image render
    resolution; try 400–600 for faint or small handwriting (slower, more RAM).

    Returns
    -------
    Tuple[str, str, Optional[bool]]
        - full_text: The concatenated OCR output from all pages.
        - output_txt_path: Absolute path to the temporary text file.
        - llm_ok: Whether local LLM extraction wrote ``review_data/<stem>_llm.json``
          (``True``), failed or was skipped (``False``/``None``). See ``SKIP_LLM``.
    """
    pdf_path = pathlib.Path(file_path).resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    LOGGER.info("Starting OCR extraction for PDF: %s", pdf_path)

    # Render DPI: 300 is standard; 400–600 can help faint pencil / small handwriting
    # (slower, more memory). Override with OCR_PDF_DPI=400 etc.
    pdf_dpi = _env_int("OCR_PDF_DPI", 300)
    pdf_dpi = max(150, min(pdf_dpi, 600))
    LOGGER.info("Rendering PDF pages at %d DPI (set OCR_PDF_DPI to adjust).", pdf_dpi)

    try:
        pages = convert_from_path(str(pdf_path), dpi=pdf_dpi)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("Failed to convert PDF to images: %s", exc)
        raise

    LOGGER.info("PDF contains %d page(s) for OCR processing.", len(pages))

    use_hw = _handwriting_assist_enabled(handwriting_assist)
    if use_hw:
        LOGGER.info(
            "Handwriting assist (EasyOCR) is enabled alongside Tesseract. "
            "First run may download models (~100MB+)."
        )

    ocr_results = []
    for idx, page in enumerate(pages, start=1):
        LOGGER.debug("Processing page %d", idx)

        # Apply Li et al. (2024)-inspired preprocessing
        preprocessed = preprocess_image(page)

        try:
            text = pytesseract.image_to_string(preprocessed, config=TESSERACT_CONFIG)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Tesseract OCR failed on page %d: %s", idx, exc)
            raise

        LOGGER.debug("Completed Tesseract for page %d (chars=%d)", idx, len(text))

        page_blocks = [f"[TESSERACT – page {idx}]\n{text.strip()}"]

        if use_hw:
            hw_text = _easyocr_text_from_image(page)
            if hw_text:
                page_blocks.append(
                    f"[EASYOCR – handwriting assist – page {idx}]\n{hw_text}"
                )
                LOGGER.debug(
                    "EasyOCR page %d: %d chars", idx, len(hw_text)
                )
            else:
                page_blocks.append(
                    f"[EASYOCR – handwriting assist – page {idx}]\n(no text)"
                )

        ocr_results.append("\n\n".join(page_blocks))

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

    llm_ok: Optional[bool] = None
    try:
        from llm_extractor import maybe_run_llm_extraction_after_ocr

        llm_ok = maybe_run_llm_extraction_after_ocr(pdf_path.stem)
    except Exception:
        LOGGER.exception("Unexpected error during LLM extraction hook")

    return full_text, str(output_path), llm_ok


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
    parser.add_argument(
        "--handwriting",
        action="store_true",
        help="Also run EasyOCR for handwriting assist (or set OCR_HANDWRITING_ASSIST=1).",
    )

    args = parser.parse_args()
    text, txt_path, llm_ok = extract_text_from_pdf(
        args.pdf_path, handwriting_assist=args.handwriting
    )
    LOGGER.info("Extraction complete. Output text file: %s", txt_path)
    print(txt_path)
    if llm_ok is True:
        LOGGER.info("LLM extraction completed (review_data JSON written).")
    elif llm_ok is False:
        LOGGER.warning(
            "LLM extraction did not complete; start Ollama or set SKIP_LLM=1 to silence."
        )

