## ML-Driven Legacy Data Migration – Ingestion & OCR Phase

This folder contains the **Ingestion and OCR** components of the capstone project, responsible for transforming scanned clinical PDFs into structured text suitable for downstream parsing into OpenEMR (e.g., demographics, medications, and problem lists).

The implementation is designed to be **modular**, **observable (logged)**, and **robust** to real-world scanner behaviour (e.g., partial writes and file locks).

---

### `ingestion_handler.py`

**Role in the pipeline**

- Acts as a **directory-watching ingestion service**.
- Monitors a configured “incoming scans” directory for new PDF files produced by scanners or legacy export processes.
- Ensures that each PDF is **fully written and stable** before it is moved and processed.
- Hands stable PDFs off to the OCR engine (`ocr_engine.py`) and logs the entire lifecycle.

**Key components**

- **`IngestionConfig` (dataclass)**  
  Encapsulates runtime configuration parameters:
  - `watch_dir`: directory to monitor for new PDFs.
  - `ingested_dir`: directory where stable PDFs are moved before OCR.
  - `stable_check_interval_secs`: frequency of file-size checks while waiting for the scanner to finish.
  - `stable_required_iterations`: how many consecutive identical size readings are required to consider a file “stable”.
  - `processing_timeout_secs`: upper bound on waiting time to avoid indefinite blocking.

- **File stability / file-lock handling (`_wait_for_file_stable`)**  
  Implements a **size-stability heuristic**:
  - Repeatedly reads the file size on disk at fixed intervals.
  - If the size remains unchanged for N consecutive checks, the file is considered stable (the scanner has likely finished writing).
  - If the timeout is exceeded, the file is logged and skipped.  
  This reduces the risk of ingesting partially written or locked files.

- **`PdfIngestionEventHandler` (Watchdog event handler)**  
  - Listens for file creation events (`on_created`) in `watch_dir`.
  - Filters events to only process `.pdf` files.
  - For each new PDF:
    1. Waits for the file to become stable.
    2. Moves the stable file into `ingested_dir`.
    3. Invokes `extract_text_from_pdf` from `ocr_engine.py`.
    4. Logs success or failure along with basic metrics (e.g., length of extracted text, path to the OCR output file).

- **`start_ingestion_service` and CLI entry point**  
  - Wires up the Watchdog `Observer` with `PdfIngestionEventHandler`.
  - Runs a long‑lived loop until interrupted (e.g., `Ctrl+C`), making it suitable as a background service or container entrypoint.
  - Can be launched via:
    - Environment variables: `INGESTION_WATCH_DIR`, `INGESTION_INGESTED_DIR`, or
    - Command line arguments:
      ```bash
      python ingestion_handler.py --watch-dir ./incoming_scans --ingested-dir ./ingested_scans
      ```

**Logging behaviour**

- Uses the standard `logging` library with informative `INFO`, `DEBUG`, and `ERROR`/`WARNING` messages.
- Logs each stage:
  - Detection of a new PDF.
  - Progress while waiting for stability.
  - File movement operations.
  - OCR invocation and completion (including basic metrics).
  - Any exceptions encountered (with stack traces for post‑hoc analysis).

---

### `ocr_engine.py`

**Role in the pipeline**

- Implements the **OCR engine** that converts scanned, multi‑page clinical PDFs into text.
- Integrates **preprocessing techniques** inspired by Li et al. (2024), including denoising and binarization, to enhance Tesseract’s recognition accuracy.
- Persists OCR output into a dedicated temporary directory for downstream parsing and quality review.

**End‑to‑end behaviour (`extract_text_from_pdf`)**

The primary public function is:

- **`extract_text_from_pdf(file_path: str) -> (full_text: str, output_txt_path: str)`**

It performs the following steps:

1. **PDF to images**  
   - Uses `pdf2image.convert_from_path` to convert every page of the PDF into a PIL image.
   - Supports multi‑page documents transparently.

2. **Image preprocessing with OpenCV**  
   For each page, the following pipeline is applied:
   - **Grayscale conversion** (`cv2.cvtColor`): reduces colour noise and simplifies the image.
   - **Denoising via Gaussian blur** (`cv2.GaussianBlur`): smooths out small scanner artefacts (e.g., speckle noise).
   - **Binarization using Otsu’s thresholding** (`cv2.threshold` with `THRESH_BINARY + THRESH_OTSU`): automatically selects an intensity threshold to segment foreground text from background, improving OCR performance on heterogeneous backgrounds.

3. **Tesseract OCR (via `pytesseract`)**  
   - Runs `pytesseract.image_to_string` on each preprocessed page.
   - Uses configuration `--oem 3 --psm 6`:
     - OEM 3: default LSTM‑based recognition engine.
     - PSM 6: assumes a uniform block of text, which is typical for clinical narratives and problem lists.
   - Concatenates page‑level results into a single string (`full_text`), preserving page separation with blank lines.

4. **Temporary text export**  
   - Ensures a `temp_extractions/` directory exists **in the same parent directory as the PDF**.
   - Writes the full OCR text to:
     - `temp_extractions/<original_pdf_stem>_ocr_<UTC_timestamp>.txt`
   - Returns both the in‑memory text and the absolute path to the saved `.txt` file.

**Supporting helpers**

- **`_ensure_output_dir(base_dir)`**  
  Creates or reuses the `temp_extractions/` subdirectory under the given base directory.

- **`_preprocess_image_for_ocr(image_bgr)`**  
  Explicitly encapsulates the grayscale → denoise → binarize pipeline for clarity and reuse.

- **PIL→OpenCV conversion helper**  
  Converts the PIL images returned by `pdf2image` into OpenCV’s BGR format, enabling the use of OpenCV’s image processing functions.

**CLI usage**

- The module also includes a small command‑line interface:

  ```bash
  python ocr_engine.py path/to/file.pdf
  ```

  This runs OCR on the specified PDF, logs progress and outcomes, and prints the path to the generated `.txt` file. It is useful for **isolated testing** of the OCR engine independent of the ingestion watcher.

---

### Typical End‑to‑End Workflow

1. **Start the ingestion service** using `ingestion_handler.py` (with appropriate directories configured).
2. **Place scanned PDF(s)** (containing demographics, medications, or problem lists) into the watched directory.
3. The ingestion service:
   - Detects each new PDF.
   - Waits until the file is fully written and stable.
   - Moves it into the ingested directory.
   - Invokes `extract_text_from_pdf` in `ocr_engine.py`.
4. **Review OCR text outputs** in the `temp_extractions/` directory under the ingested directory, and feed them into downstream parsers that transform free‑text into structured OpenEMR data models.

This separation of concerns (ingestion vs. OCR) and the explicit preprocessing pipeline make the implementation suitable for inclusion in the **Methods** section of an academic capstone report, and future extensions (e.g., language models for concept extraction) can be layered on top without modifying the ingestion/OCR infrastructure.

