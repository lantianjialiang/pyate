"""Batch execution: define, expand, run and aggregate multi-job runs."""

from __future__ import annotations

from .queue import Job, JobQueue, JobStateError, JobStatus
from .report import aggregate_report, load_capability, merge_capability
from .retest import CONTACT_FAIL_BIN, RetestConfig, merge_attempt, should_retest
from .runner import BatchRunner, BatchSummary, JobResult, run_job, write_batch_csv
from .spec import BatchSpec, BatchSpecError, JobSpec, expand_jobs, load_batch

__all__ = [
    "BatchRunner",
    "BatchSpec",
    "BatchSpecError",
    "BatchSummary",
    "CONTACT_FAIL_BIN",
    "Job",
    "JobQueue",
    "JobResult",
    "JobSpec",
    "JobStateError",
    "JobStatus",
    "RetestConfig",
    "aggregate_report",
    "expand_jobs",
    "load_batch",
    "load_capability",
    "merge_attempt",
    "merge_capability",
    "run_job",
    "should_retest",
    "write_batch_csv",
]
