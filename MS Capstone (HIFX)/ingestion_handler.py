"""
ingestion_handler.py
---------------------

Directory-watching ingestion module for the ML-driven legacy data migration
pipeline targeting OpenEMR.

Responsibilities:
- Monitor a configured directory for newly scanned PDF files.
- Safely handle file-lock and partial-write conditions from scanners by
  waiting until files are stable before moving/processing them.
- Move completed scans into a designated "ingested" directory.
- Trigger OCR via the `ocr_engine` module for each ingested PDF.
- Provide robust, structured logging suitable for academic documentation.

This module is designed to be run as a long-lived process (e.g., a service
or background job) within the capstone project environment.
"""

import logging
import os
import pathlib
import shutil
import time
from dataclasses import dataclass
from typing import Optional

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from ocr_engine import extract_text_from_pdf
from patient_chart import build_patient_chart


LOGGER = logging.getLogger(__name__)


@dataclass
class IngestionConfig:
    """
    Configuration parameters for the ingestion handler.

    Attributes
    ----------
    watch_dir : str
        Directory to monitor for new PDF files produced by scanners.
    processed_dir : str
        Directory to which fully written PDF files are moved before OCR.
    stable_check_interval_secs : float
        Interval between file-size checks when waiting for a file to become
        stable (i.e., no longer being written by the scanner).
    stable_required_iterations : int
        Number of consecutive checks with unchanged file size required before
        the file is considered safe to process.
    processing_timeout_secs : float
        Maximum time to wait for a file to become stable before giving up.
    """

    watch_dir: str
    processed_dir: str
    stable_check_interval_secs: float = 1.0
    stable_required_iterations: int = 3
    processing_timeout_secs: float = 300.0  # 5 minutes as a safety cap


def _ensure_directory(path_str: str) -> pathlib.Path:
    """
    Ensure that a directory exists, creating it (and parents) if needed.

    Parameters
    ----------
    path_str : str
        Directory path to validate or create.

    Returns
    -------
    pathlib.Path
        The resolved path object for the directory.
    """
    path = pathlib.Path(path_str).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _wait_for_file_stable(
    file_path: pathlib.Path,
    check_interval_secs: float,
    required_iterations: int,
    timeout_secs: float,
) -> bool:
    """
    Wait until a file's size remains stable for a specified number of checks.

    This heuristic mitigates issues where a scanner or external system is
    still writing to the file when the filesystem event is triggered.

    Parameters
    ----------
    file_path : pathlib.Path
        Path to the file whose stability is being monitored.
    check_interval_secs : float
        Delay (in seconds) between consecutive size checks.
    required_iterations : int
        Number of consecutive checks with unchanged size required to
        consider the file stable.
    timeout_secs : float
        Maximum waiting time in seconds before aborting.

    Returns
    -------
    bool
        True if the file became stable within the timeout; False otherwise.
    """
    LOGGER.info("Waiting for file to become stable: %s", file_path)
    stable_count = 0
    last_size: Optional[int] = None
    start_time = time.time()

    while time.time() - start_time < timeout_secs:
        try:
            current_size = file_path.stat().st_size
        except FileNotFoundError:
            LOGGER.warning(
                "File disappeared while waiting for stability: %s", file_path
            )
            return False

        if last_size is None:
            last_size = current_size
            stable_count = 1
        elif current_size == last_size:
            stable_count += 1
            LOGGER.debug(
                "File size stable (%d/%d) for %s: %d bytes",
                stable_count,
                required_iterations,
                file_path,
                current_size,
            )
            if stable_count >= required_iterations:
                LOGGER.info("File is now stable: %s", file_path)
                return True
        else:
            LOGGER.debug(
                "File size changed for %s: %d -> %d bytes; resetting counter",
                file_path,
                last_size,
                current_size,
            )
            last_size = current_size
            stable_count = 1

        time.sleep(check_interval_secs)

    LOGGER.error(
        "Timed out waiting for file to become stable (timeout=%ss): %s",
        timeout_secs,
        file_path,
    )
    return False


class PdfIngestionEventHandler(FileSystemEventHandler):
    """
    Watchdog event handler responsible for reacting to new PDF files.
    """

    def __init__(self, config: IngestionConfig):
        super().__init__()
        self.config = config
        self.watch_dir = _ensure_directory(config.watch_dir)
        self.processed_dir = _ensure_directory(config.processed_dir)

    def on_created(self, event):
        """
        React to file creation events.

        Only PDF files are processed; other file types are ignored.
        """
        # We are only interested in files (not directories)
        if event.is_directory:
            return

        if not isinstance(event, FileCreatedEvent):
            return

        src_path = pathlib.Path(event.src_path)

        # Filter for PDF files
        if src_path.suffix.lower() != ".pdf":
            LOGGER.debug("Ignoring non-PDF file: %s", src_path)
            return

        LOGGER.info("Detected new PDF file: %s", src_path)
        self._handle_new_pdf(src_path)

    def _handle_new_pdf(self, src_path: pathlib.Path) -> None:
        """
        Handle the full ingestion lifecycle for a new PDF file:
        - Wait for the file to become stable.
        - Move it to the ingested directory.
        - Invoke OCR to extract text.
        """
        # Wait until the scanner finishes writing the file
        stable = _wait_for_file_stable(
            file_path=src_path,
            check_interval_secs=self.config.stable_check_interval_secs,
            required_iterations=self.config.stable_required_iterations,
            timeout_secs=self.config.processing_timeout_secs,
        )

        if not stable:
            LOGGER.error(
                "Skipping file due to instability/timeout: %s", src_path
            )
            return

        # Move the PDF into the processed directory for downstream processing
        destination = self.processed_dir / src_path.name

        try:
            LOGGER.info(
                "Moving PDF to processed directory: %s -> %s",
                src_path,
                destination,
            )
            shutil.move(str(src_path), str(destination))
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception(
                "Failed to move file %s to %s: %s", src_path, destination, exc
            )
            return

        # Trigger OCR extraction for the moved file
        self.process_file(destination)

    def process_file(self, pdf_path: pathlib.Path) -> None:
        """
        Run OCR on a processed PDF file and handle any OCR-related errors.

        This separation allows easier testing and aligns with the
        "extraction engine" abstraction in the capstone design.
        """
        try:
            LOGGER.info("Starting OCR for processed PDF: %s", pdf_path)
            full_text, txt_path, llm_ok, pdf_final = extract_text_from_pdf(str(pdf_path))
            pdf_path = pathlib.Path(pdf_final)
            LOGGER.info(
                "OCR completed for %s; text length=%d; txt_path=%s; llm_ok=%s",
                pdf_path.name,
                len(full_text),
                txt_path,
                llm_ok,
            )
            if llm_ok is False:
                LOGGER.warning(
                    "LLM extraction failed for %s — ensure Ollama is running or set SKIP_LLM=1",
                    pdf_path.name,
                )
            # Build FHIR Bundle for patient chart (pass OCR text to avoid re-reading)
            try:
                chart_path = build_patient_chart(
                    pdf_path.stem, source_document=pdf_path.name, ocr_text=full_text
                )
                LOGGER.info("FHIR Bundle written: %s", chart_path)
            except Exception as chart_exc:  # pragma: no cover
                LOGGER.warning(
                    "Patient chart build failed for %s: %s", pdf_path.name, chart_exc
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception(
                "OCR extraction failed for %s: %s", pdf_path, exc
            )


def start_ingestion_service(config: IngestionConfig) -> None:
    """
    Start the directory watcher for PDF ingestion.

    This function configures the Watchdog observer, attaches the
    `PdfIngestionEventHandler`, and blocks the current thread until a keyboard
    interrupt occurs.
    """
    watch_path = _ensure_directory(config.watch_dir)
    LOGGER.info("Starting ingestion service. Watching directory: %s", watch_path)

    event_handler = PdfIngestionEventHandler(config=config)
    observer = Observer()
    observer.schedule(event_handler, str(watch_path), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        LOGGER.info("Keyboard interrupt received; stopping ingestion service.")
    finally:
        observer.stop()
        observer.join()
        LOGGER.info("Ingestion service stopped.")


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Directory-watching ingestion service for scanned clinical PDFs."
    )
    parser.add_argument(
        "--watch-dir",
        type=str,
        required=False,
        default=os.environ.get("INGESTION_WATCH_DIR", "./incoming_scans"),
        help=(
            "Directory to watch for new scanned PDFs. "
            "Defaults to './incoming_scans' or the value of INGESTION_WATCH_DIR."
        ),
    )
    parser.add_argument(
        "--processed-dir",
        type=str,
        required=False,
        default=os.environ.get("INGESTION_PROCESSED_DIR", "./processed_scans"),
        help=(
            "Directory to which stable PDFs are moved before OCR. "
            "Defaults to './processed_scans' or the value of INGESTION_PROCESSED_DIR."
        ),
    )

    args = parser.parse_args()

    ingestion_config = IngestionConfig(
        watch_dir=args.watch_dir,
        processed_dir=args.processed_dir,
    )

    start_ingestion_service(ingestion_config)

