"""Background ingestion jobs for the admin API.

One job at a time per process. A job runs the normal streaming pipeline in a daemon thread
against the *serving* store object, so the running API sees the new chunks immediately (and,
for a full rebuild, the recreated collection) without a restart. Progress is read from the
state DB's ``runs`` table, which the pipeline updates after every flushed batch.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from securerag.config import Settings
from securerag.ingestion.state import IngestState

logger = logging.getLogger(__name__)


class JobConflict(RuntimeError):
    """Another ingestion job is still running."""


class IngestJobManager:
    def __init__(self, settings: Settings, encryptor: Any, store: Any, encoder: Any):
        self.settings = settings
        self.encryptor = encryptor
        self.store = store
        self.encoder = encoder
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._errors: dict[str, str] = {}

    def start(self, *, full_rebuild: bool = False) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise JobConflict("an ingestion job is already running")
            job_id = uuid.uuid4().hex[:12]
            self._thread = threading.Thread(target=self._run, args=(job_id, full_rebuild),
                                            name=f"ingest-{job_id}", daemon=True)
            self._thread.start()
            return job_id

    def _run(self, job_id: str, full_rebuild: bool) -> None:
        from securerag.retrieval.store import run_ingestion

        try:
            run_ingestion(self.settings, self.encryptor, full_rebuild=full_rebuild, store=self.store,
                          encoder=self.encoder, run_id=job_id)
        except Exception as exc:  # noqa: BLE001 - recorded for the status endpoint
            logger.exception("ingestion job %s failed", job_id)
            self._errors[job_id] = type(exc).__name__

    def status(self, job_id: str) -> dict[str, Any] | None:
        state = IngestState(self.settings.resolved_state_db_path)
        try:
            run = state.get_run(job_id)
        finally:
            state.close()
        if run is None:
            if self._thread is not None and self._thread.name == f"ingest-{job_id}" and self._thread.is_alive():
                return {"run_id": job_id, "status": "starting", "stats": {}}
            return None
        if job_id in self._errors:
            run["error"] = self._errors[job_id]
        return run

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
