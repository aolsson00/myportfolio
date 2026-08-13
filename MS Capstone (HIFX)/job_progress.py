"""
job_progress.py
---------------

In-memory job status for async upload / FHIR steps (local single-user app).
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Optional

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def create_job() -> str:
    jid = str(uuid.uuid4())
    with _lock:
        _jobs[jid] = {
            "status": "pending",
            "step": "queued",
            "percent": 0,
            "message": "Queued…",
            "error": None,
            "redirect": None,
            "flash": [],
        }
    return jid


def update(
    jid: str,
    step: str,
    percent: int,
    message: str,
) -> None:
    with _lock:
        if jid not in _jobs:
            return
        _jobs[jid].update(
            {
                "status": "processing",
                "step": step,
                "percent": max(0, min(100, int(percent))),
                "message": message,
            }
        )


def complete(
    jid: str,
    *,
    redirect: Optional[str] = None,
    flash: Optional[list[tuple[str, str]]] = None,
) -> None:
    with _lock:
        if jid not in _jobs:
            return
        _jobs[jid].update(
            {
                "status": "complete",
                "step": "done",
                "percent": 100,
                "message": "Complete",
                "redirect": redirect,
                "flash": flash or [],
                "error": None,
            }
        )


def fail(jid: str, error: str) -> None:
    with _lock:
        if jid not in _jobs:
            return
        _jobs[jid].update(
            {
                "status": "error",
                "step": "error",
                "percent": 0,
                "message": error,
                "error": error,
                "redirect": None,
            }
        )


def get_job(jid: str) -> Optional[dict[str, Any]]:
    with _lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def delete_job(jid: str) -> None:
    with _lock:
        _jobs.pop(jid, None)
