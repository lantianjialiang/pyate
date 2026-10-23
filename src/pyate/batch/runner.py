"""Batch runner: execute many jobs (serial / thread pool / process pool)."""

from __future__ import annotations

import csv
import json
import logging
import threading
import time
import traceback
from collections.abc import Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.loader import load_session
from ..datalog.factory import build_sinks
from ..engine.controller import RunController
from ..engine.events import EventBus, EventKind
from .queue import Job, JobQueue, JobStatus
from .report import aggregate_report
from .retest import RetestConfig
from .spec import BatchSpec, JobSpec

__all__ = ["JobResult", "BatchSummary", "BatchRunner", "run_job"]

_LOG = logging.getLogger("pyate.batch")


@dataclass
class JobResult:
    """Outcome of one batch job."""

    job_id: str
    status: str
    session: str = ""
    stage: str = ""
    output_dir: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0
    total_parts: int = 0
    tested: int = 0
    passed: int = 0
    yield_pct: float = 0.0
    bin_counts: dict[int, int] = field(default_factory=dict)
    uph: float = 0.0
    retests: int = 0
    stdf_files: list[str] = field(default_factory=list)
    summary_path: str = ""
    stats_path: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        """Wall clock duration."""
        end = self.finished_at or self.started_at
        return max(0.0, end - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "session": self.session,
            "stage": self.stage,
            "output_dir": self.output_dir,
            "labels": dict(self.labels),
            "overrides": dict(self.overrides),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "total_parts": self.total_parts,
            "tested": self.tested,
            "passed": self.passed,
            "yield_pct": round(self.yield_pct, 3),
            "bin_counts": {str(k): v for k, v in sorted(self.bin_counts.items())},
            "uph": round(self.uph, 2),
            "retests": self.retests,
            "stdf_files": list(self.stdf_files),
            "summary_path": self.summary_path,
            "stats_path": self.stats_path,
            "error": self.error,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobResult":
        """Rebuild a result from its serialised form."""
        bins = {int(k): int(v) for k, v in (data.get("bin_counts") or {}).items()}
        return cls(
            job_id=str(data.get("job_id", "")),
            status=str(data.get("status", "DONE")),
            session=str(data.get("session", "")),
            stage=str(data.get("stage", "")),
            output_dir=str(data.get("output_dir", "")),
            labels=dict(data.get("labels") or {}),
            overrides=dict(data.get("overrides") or {}),
            started_at=float(data.get("started_at", 0.0) or 0.0),
            finished_at=float(data.get("finished_at", 0.0) or 0.0),
            total_parts=int(data.get("total_parts", 0) or 0),
            tested=int(data.get("tested", 0) or 0),
            passed=int(data.get("passed", 0) or 0),
            yield_pct=float(data.get("yield_pct", 0.0) or 0.0),
            bin_counts=bins,
            uph=float(data.get("uph", 0.0) or 0.0),
            retests=int(data.get("retests", 0) or 0),
            stdf_files=list(data.get("stdf_files") or []),
            summary_path=str(data.get("summary_path", "")),
            stats_path=str(data.get("stats_path", "")),
            error=str(data.get("error", "")),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class BatchSummary:
    """Aggregated result of a whole batch."""

    batch_id: str
    started_at: float
    finished_at: float = 0.0
    jobs: list[JobResult] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    output_dir: str = ""
    aborted: bool = False

    @property
    def duration_s(self) -> float:
        """Wall clock duration."""
        end = self.finished_at or self.started_at
        return max(0.0, end - self.started_at)

    @property
    def failed(self) -> list[JobResult]:
        """Jobs that finished in a failure state."""
        return [job for job in self.jobs
                if job.status in (JobStatus.FAILED.value, JobStatus.ABORTED.value)]

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view (aggregates included)."""
        report = aggregate_report(self.jobs)
        return {
            "batch_id": self.batch_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "aborted": self.aborted,
            "counts": dict(self.counts),
            "aggregate": report,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    def write(self, output_dir: str | Path | None = None) -> dict[str, Path]:
        """Write ``batch_summary.json``, ``batch_summary.csv`` and ``batch_report.md``."""
        out = Path(output_dir or self.output_dir or ".")
        out.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        json_path = out / "batch_summary.json"
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        csv_path = write_batch_csv(self.jobs, out / "batch_summary.csv")
        md_path = out / "batch_report.md"
        md_path.write_text(render_markdown(data), encoding="utf-8")
        return {"json": json_path, "csv": csv_path, "report": md_path}


def write_batch_csv(jobs: Sequence[JobResult], path: str | Path) -> Path:
    """Write the per-job table as CSV."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = ["job_id", "status", "stage", "session", "total_parts", "tested", "passed",
              "yield_pct", "uph", "retests", "duration_s", "stdf_files", "output_dir", "error"]
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for job in jobs:
            writer.writerow([
                job.job_id, job.status, job.stage, job.session, job.total_parts, job.tested,
                job.passed, round(job.yield_pct, 3), round(job.uph, 2), job.retests,
                round(job.duration_s, 3), ";".join(job.stdf_files), job.output_dir, job.error,
            ])
    return target


def render_markdown(data: dict[str, Any]) -> str:
    """Render the batch summary as a small markdown report."""
    agg = data.get("aggregate", {})
    lines = [
        f"# Batch {data.get('batch_id')}",
        "",
        f"- jobs: {len(data.get('jobs', []))}",
        f"- duration: {data.get('duration_s')} s",
        f"- counts: {data.get('counts')}",
        f"- yield: {agg.get('yield_pct')} % ({agg.get('passed')}/{agg.get('tested')})",
        f"- uph: {agg.get('uph_avg')}",
        "",
        "| job | status | parts | yield % | uph | stdf |",
        "|---|---|---|---|---|---|",
    ]
    for job in data.get("jobs", []):
        lines.append(
            f"| {job.get('job_id')} | {job.get('status')} | {job.get('tested')} | "
            f"{job.get('yield_pct')} | {job.get('uph')} | "
            f"{len(job.get('stdf_files') or [])} file(s) |"
        )
    pareto = agg.get("bin_pareto") or []
    if pareto:
        lines += ["", "## Bin pareto", "", "| bin | count | pct |", "|---|---|---|"]
        lines += [f"| {row['bin']} | {row['count']} | {row['pct']} |" for row in pareto]
    caps = [row for row in (agg.get("capability") or []) if row.get("count")]
    if caps:
        lines += ["", "## Capability (Cpk)", "",
                  "| test | name | n | mean | sigma | cpk |", "|---|---|---|---|---|---|"]
        for row in caps:
            lines.append(
                f"| {row['test_num']} | {row.get('name', '')} | {row['count']} | "
                f"{row['mean']:.4g} | {row['sigma']:.4g} | {row['cpk']:.3f} |"
            )
    return "\n".join(lines) + "\n"


def run_job(
    session_path: str | Path,
    overrides: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
    *,
    stage: str | None = None,
    retest: RetestConfig | None = None,
    seed: int | None = None,
    stdf: bool = True,
) -> JobResult:
    """Run one job synchronously and return its result.

    Importable and picklable helper: the process pool calls this function.
    """
    started = time.time()
    session = load_session(session_path, overrides or {})
    run_stage = stage or session.stage
    out = Path(output_dir) if output_dir is not None else session.output_dir
    out.mkdir(parents=True, exist_ok=True)
    site_count = session.site_count()
    policy = retest or RetestConfig.from_session(session)
    sinks = build_sinks(session, stage=run_stage, output_dir=out,
                        site_count=site_count, stdf=stdf)
    controller = RunController(
        session,
        site_count=site_count,
        stage=run_stage,
        seed=seed,
        output_dir=out,
        sinks=sinks,
        retest_bins=policy.bins,
        max_retries=policy.max_retries,
        resume=False,
    )
    summary = controller.run()
    stdf_files = [
        str(path)
        for sink in sinks if type(sink).__name__ == "StdfSink"
        for path in getattr(sink, "files", []) or []
    ]
    stats_path = ""
    for sink in sinks:
        path = getattr(sink, "path", None)
        if path is not None and str(path).endswith("stats.json"):
            stats_path = str(path)
    return JobResult(
        job_id="",
        status=JobStatus.DONE.value,
        session=str(session_path),
        stage=run_stage,
        output_dir=str(out),
        started_at=started,
        finished_at=time.time(),
        total_parts=summary.total_parts,
        tested=summary.total_tested,
        passed=summary.total_passed,
        yield_pct=summary.yield_pct,
        bin_counts=dict(summary.bin_counts),
        uph=summary.uph,
        retests=int(summary.meta.get("retests", 0) or 0),
        stdf_files=stdf_files,
        summary_path=str(summary.files.get("summary", "")),
        stats_path=stats_path,
        meta={"aborted": summary.aborted},
    )


def _job_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry point: run one job and return its serialised result."""
    try:
        result = run_job(
            payload["session"],
            payload.get("overrides") or {},
            payload.get("output_dir"),
            stage=payload.get("stage") or None,
            seed=payload.get("seed"),
        )
        result.job_id = payload["job_id"]
        result.labels = payload.get("labels") or {}
        result.overrides = payload.get("overrides") or {}
        return result.to_dict()
    except Exception as exc:  # noqa: BLE001 - failure must not kill the pool
        return {
            "job_id": payload.get("job_id", "?"),
            "status": JobStatus.FAILED.value,
            "session": payload.get("session", ""),
            "output_dir": payload.get("output_dir", ""),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=6),
        }


class BatchRunner:
    """Runs every job of a batch and aggregates the results."""

    def __init__(
        self,
        spec: BatchSpec,
        jobs: Sequence[JobSpec] | None = None,
        *,
        events: EventBus | None = None,
        output_dir: str | Path | None = None,
        retest: RetestConfig | None = None,
        seed: int | None = None,
    ) -> None:
        from .spec import expand_jobs

        self.spec = spec
        self.job_specs = list(jobs) if jobs is not None else expand_jobs(spec)
        self.events = events
        base = Path(output_dir) if output_dir is not None else Path(spec.output_dir)
        self.output_dir = base if base.is_absolute() else (spec.root / base).resolve()
        self.retest = retest
        self.seed = seed
        self.queue = JobQueue(self.job_specs)
        self._cancel = threading.Event()
        self.batch_id = spec.batch_id
        self.summary: BatchSummary | None = None

    # ---------------------------------------------------------------- control
    def cancel(self) -> None:
        """Stop scheduling new jobs (running jobs finish normally)."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` was called."""
        return self._cancel.is_set()

    def _publish(self, kind: EventKind, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(kind, payload)

    def job_dir(self, job: Job) -> Path:
        """Output directory of one job (``out/<batch_id>/<job_id>``)."""
        return self.output_dir / job.id

    # -------------------------------------------------------------------- run
    def run(self) -> BatchSummary:
        """Execute all jobs and return the batch summary."""
        started = time.time()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._publish(EventKind.LOT_START, {
            "batch": self.batch_id, "jobs": len(self.job_specs),
            "executor": self.spec.executor, "max_workers": self.spec.max_workers,
        })
        try:
            executor_kind = self.spec.executor
            if executor_kind == "serial" or len(self.job_specs) == 1:
                self._run_serial()
            else:
                pool_cls: type[Executor] = (
                    ProcessPoolExecutor if executor_kind == "process" else ThreadPoolExecutor
                )
                with pool_cls(max_workers=self.spec.max_workers) as pool:
                    self._run_pool(pool)
        finally:
            summary = BatchSummary(
                batch_id=self.batch_id,
                started_at=started,
                finished_at=time.time(),
                jobs=[self._to_result(job) for job in self.queue.jobs],
                counts=self.queue.counts(),
                output_dir=str(self.output_dir),
                aborted=self.cancelled,
            )
            summary.write(self.output_dir)
            self.summary = summary
            self._publish(EventKind.LOT_END, summary.to_dict())
        return summary

    def _run_serial(self) -> None:
        while not self._cancel.is_set():
            job = self.queue.claim()
            if job is None:
                break
            self._execute(job)

    def _run_pool(self, pool: Executor) -> None:
        futures = {}
        while True:
            if self._cancel.is_set():
                break
            job = self.queue.claim()
            if job is not None:
                job.output_dir = str(self.job_dir(job))
                payload = {
                    "job_id": job.id,
                    "session": job.spec.session,
                    "overrides": job.spec.merged(),
                    "output_dir": job.output_dir,
                    "labels": job.spec.labels,
                    "seed": self.seed,
                }
                futures[pool.submit(_job_entry, payload)] = job
                continue
            if not futures:
                break
            done = [future for future in futures if future.done()]
            if not done:
                time.sleep(0.02)
                continue
            for future in done:
                job = futures.pop(future)
                self._apply_pool_result(job, future)

    def _apply_pool_result(self, job: Job, future: Any) -> None:
        try:
            data = future.result()
        except Exception as exc:  # noqa: BLE001 - isolate worker crashes
            job.transition(JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            self._publish(EventKind.ERROR, {"batch": self.batch_id, "job": job.id,
                                            "error": job.error})
            self._fail_fast()
            return
        status = JobStatus(str(data.get("status", JobStatus.DONE.value)))
        job.output_dir = str(data.get("output_dir", job.output_dir))
        job.result = data
        job.transition(status, error=str(data.get("error", "")))
        self._publish(EventKind.PROGRESS, {
            "batch": self.batch_id, "job": job.id, "status": status.value,
            "yield_pct": data.get("yield_pct", 0.0),
        })
        if status is JobStatus.FAILED:
            self._fail_fast()

    def _fail_fast(self) -> None:
        if self.spec.fail_fast:
            self._cancel.set()
            self.queue.abort_pending()

    def _execute(self, job: Job) -> None:
        job.output_dir = str(self.job_dir(job))
        self._publish(EventKind.PROGRESS, {"batch": self.batch_id, "job": job.id,
                                           "status": JobStatus.RUNNING.value})
        try:
            result = run_job(
                job.spec.session,
                job.spec.merged(),
                job.output_dir,
                retest=self.retest,
                seed=self.seed,
            )
        except Exception as exc:  # noqa: BLE001 - a failing job must not stop the batch
            _LOG.exception("job %s failed", job.id)
            job.transition(JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            self._publish(EventKind.ERROR, {"batch": self.batch_id, "job": job.id,
                                            "error": job.error})
            self._fail_fast()
            return
        result.job_id = job.id
        result.labels = dict(job.spec.labels)
        result.overrides = dict(job.spec.merged())
        job.result = result.to_dict()
        job.transition(JobStatus.DONE)
        self._publish(EventKind.PROGRESS, {
            "batch": self.batch_id, "job": job.id, "status": JobStatus.DONE.value,
            "yield_pct": result.yield_pct,
        })

    @staticmethod
    def _to_result(job: Job) -> JobResult:
        data = dict(job.result)
        if not data:
            return JobResult(job_id=job.id, status=job.status.value,
                             session=job.spec.session, output_dir=job.output_dir,
                             labels=dict(job.spec.labels),
                             overrides=dict(job.spec.merged()),
                             started_at=job.started_at, finished_at=job.finished_at,
                             error=job.error)
        result = JobResult.from_dict(data)
        result.job_id = job.id
        result.status = job.status.value
        result.session = result.session or job.spec.session
        result.output_dir = result.output_dir or job.output_dir
        result.labels = dict(job.spec.labels)
        result.started_at = result.started_at or job.started_at
        result.finished_at = result.finished_at or job.finished_at
        if job.error and not result.error:
            result.error = job.error
        return result
