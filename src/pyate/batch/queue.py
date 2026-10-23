"""Job queue and state machine for batch execution."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .spec import JobSpec

__all__ = ["JobStatus", "Job", "JobQueue"]


class JobStatus(str, Enum):
    """Lifecycle of one batch job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    SKIPPED = "SKIPPED"


_ALLOWED: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.SKIPPED, JobStatus.ABORTED},
    JobStatus.RUNNING: {JobStatus.DONE, JobStatus.FAILED, JobStatus.ABORTED},
    JobStatus.DONE: set(),
    JobStatus.FAILED: {JobStatus.RUNNING},
    JobStatus.ABORTED: {JobStatus.RUNNING},
    JobStatus.SKIPPED: {JobStatus.RUNNING},
}


class JobStateError(RuntimeError):
    """Raised on an illegal job state transition."""


@dataclass
class Job:
    """One queued job with its runtime state."""

    spec: JobSpec
    status: JobStatus = JobStatus.PENDING
    started_at: float = 0.0
    finished_at: float = 0.0
    output_dir: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Job id."""
        return self.spec.id

    @property
    def running(self) -> bool:
        """True while the job executes."""
        return self.status is JobStatus.RUNNING

    @property
    def finished(self) -> bool:
        """True once the job reached a terminal state."""
        return self.status in (JobStatus.DONE, JobStatus.FAILED,
                               JobStatus.ABORTED, JobStatus.SKIPPED)

    @property
    def duration_s(self) -> float:
        """Wall clock duration of the job."""
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def transition(self, status: JobStatus, *, error: str = "") -> None:
        """Move to ``status``, validating the transition."""
        if status is self.status:
            return
        if status not in _ALLOWED[self.status]:
            raise JobStateError(f"job {self.id}: cannot go {self.status.value} -> {status.value}")
        self.status = status
        if error:
            self.error = error
        if status is JobStatus.RUNNING:
            self.started_at = time.time()
            self.finished_at = 0.0
        elif status in (JobStatus.DONE, JobStatus.FAILED,
                        JobStatus.ABORTED, JobStatus.SKIPPED):
            self.finished_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view."""
        data: dict[str, Any] = {
            "job_id": self.id,
            "session": self.spec.session,
            "status": self.status.value,
            "labels": dict(self.spec.labels),
            "overrides": dict(self.spec.overrides),
            "output_dir": self.output_dir,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
        }
        if self.error:
            data["error"] = self.error
        if self.result:
            data["result"] = dict(self.result)
        return data


class JobQueue:
    """Thread safe queue of :class:`Job` objects."""

    def __init__(self, jobs: Iterable[JobSpec]) -> None:
        self._jobs: list[Job] = [Job(spec=spec) for spec in jobs]
        self._lock = threading.RLock()

    def __len__(self) -> int:
        return len(self._jobs)

    def __iter__(self) -> Any:
        return iter(self._jobs)

    @property
    def jobs(self) -> list[Job]:
        """All jobs in definition order."""
        with self._lock:
            return list(self._jobs)

    def get(self, job_id: str) -> Job:
        """Look up a job by id."""
        for job in self._jobs:
            if job.id == job_id:
                return job
        raise KeyError(job_id)

    def pending(self) -> list[Job]:
        """Jobs still waiting to run."""
        with self._lock:
            return [job for job in self._jobs if job.status is JobStatus.PENDING]

    def claim(self) -> Job | None:
        """Atomically take the next pending job and mark it RUNNING."""
        with self._lock:
            for job in self._jobs:
                if job.status is JobStatus.PENDING:
                    job.transition(JobStatus.RUNNING)
                    return job
        return None

    def abort_pending(self) -> int:
        """Mark every still pending job as ABORTED (used by ``--fail-fast``)."""
        count = 0
        with self._lock:
            for job in self._jobs:
                if job.status is JobStatus.PENDING:
                    job.transition(JobStatus.ABORTED, error="aborted by fail-fast")
                    count += 1
        return count

    def counts(self) -> dict[str, int]:
        """Number of jobs per status."""
        out: dict[str, int] = {status.value: 0 for status in JobStatus}
        for job in self._jobs:
            out[job.status.value] += 1
        return out
