"""Qt-free view model for the batch panel."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...api import BatchRunner, BatchSpec, EventBus, JobSpec, load_batch

__all__ = ["BatchJobRow", "BatchViewModel"]


@dataclass
class BatchJobRow:
    """One row of the batch progress table."""

    job_id: str
    status: str = "PENDING"
    parts: int = 0
    yield_pct: float = 0.0
    duration_s: float = 0.0
    output_dir: str = ""
    error: str = ""


class BatchViewModel:
    """Loads a batch file and runs it on a background thread."""

    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()
        self.spec: BatchSpec | None = None
        self.jobs: list[JobSpec] = []
        self.rows: list[BatchJobRow] = []
        self.runner: BatchRunner | None = None
        self.error = ""
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------- load
    def load(self, path: str | Path) -> list[BatchJobRow]:
        """Parse ``batch.yaml`` and return the expanded job list."""
        try:
            self.spec, self.jobs = load_batch(path)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.spec, self.jobs = None, []
            self.error = str(exc)
            self.rows = []
            return []
        self.error = ""
        self.rows = [BatchJobRow(job_id=job.id, output_dir=str(job.session))
                     for job in self.jobs]
        return self.rows

    # -------------------------------------------------------------------- run
    def start(self, *, workers: int | None = None, fail_fast: bool = False,
              output_dir: str | Path | None = None) -> bool:
        """Start the batch on a daemon thread."""
        if self.spec is None or not self.jobs:
            return False
        spec = self.spec
        if workers:
            spec = spec.model_copy(update={"max_workers": int(workers)})
        if fail_fast:
            spec = spec.model_copy(update={"fail_fast": True})
        self.runner = BatchRunner(spec, self.jobs, events=self.events, output_dir=output_dir)
        self._thread = threading.Thread(target=self.runner.run, name="pyate-batch", daemon=True)
        self._thread.start()
        return True

    @property
    def running(self) -> bool:
        """True while the batch thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def poll(self) -> list[BatchJobRow]:
        """Refresh the row list from the runner state."""
        if self.runner is None:
            return self.rows
        rows: list[BatchJobRow] = []
        for job in self.runner.queue.jobs:
            result = job.result or {}
            rows.append(BatchJobRow(
                job_id=job.id,
                status=job.status.value,
                parts=int(result.get("tested", 0) or 0),
                yield_pct=float(result.get("yield_pct", 0.0) or 0.0),
                duration_s=round(job.duration_s, 2),
                output_dir=job.output_dir,
                error=job.error,
            ))
        self.rows = rows
        return rows

    def wait(self, timeout: float | None = None, poll_s: float = 0.05) -> None:
        """Block until the batch finished (used by tests)."""
        deadline = None if timeout is None else time.time() + timeout
        while self.running:
            if deadline is not None and time.time() > deadline:
                return
            time.sleep(poll_s)
        self.poll()

    # ----------------------------------------------------------------- report
    def summary(self) -> dict[str, Any]:
        """Batch summary (empty while the batch has not finished)."""
        if self.runner is None or self.runner.summary is None:
            return {}
        return self.runner.summary.to_dict()

    def aggregate_text(self) -> str:
        """Short aggregate description for the status bar."""
        data = self.summary()
        if not data:
            return "batch not finished"
        agg = data.get("aggregate", {})
        return (f"jobs {agg.get('done')}/{agg.get('jobs')}  "
                f"yield {agg.get('yield_pct')}%  uph {agg.get('uph_avg')}  "
                f"stdf {agg.get('stdf_files')}")

    def cancel(self) -> None:
        """Stop scheduling new jobs."""
        if self.runner is not None:
            self.runner.cancel()
