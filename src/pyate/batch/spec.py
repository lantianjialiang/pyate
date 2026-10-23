"""Batch definition (``batch.yaml``) and sweep expansion.

A batch is a list of jobs. Every job is a session file plus a set of overrides
(same dotted-path syntax as ``--set``). Sweeps turn one axis definition into the
cartesian product of all axes, so ``3 temperatures x 2 profiles`` becomes 6 jobs
without writing them by hand.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RetestPolicy",
    "JobSpec",
    "BatchSpec",
    "BatchSpecError",
    "load_batch",
    "expand_jobs",
]


class BatchSpecError(ValueError):
    """Raised when a ``batch.yaml`` file cannot be loaded or expanded."""


class RetestPolicy(BaseModel):
    """Retest settings shared by every job of a batch."""

    model_config = ConfigDict(extra="forbid")

    bins: list[int] = Field(default_factory=list)
    max_retries: int = 0
    on: list[str] = Field(default_factory=list)
    keep: str = "last"

    def bins_for(self, contact_fail: bool = False) -> list[int]:
        """Soft bins that trigger a retest (contact failures are implicit)."""
        bins = list(self.bins)
        if contact_fail and "contact_fail" in self.on and 2 not in bins:
            bins.append(2)
        return bins


class JobSpec(BaseModel):
    """One runnable job: a session path plus overrides."""

    model_config = ConfigDict(extra="forbid")

    id: str
    session: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    note: str = ""

    def merged(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Overrides with an optional extra mapping applied on top."""
        out = dict(self.overrides)
        out.update(extra or {})
        return out


class BatchSpec(BaseModel):
    """Parsed ``batch.yaml``."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str = "BATCH"
    session: str | None = None
    sessions: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)
    sweep: dict[str, list[Any]] = Field(default_factory=dict)
    jobs: list[JobSpec] = Field(default_factory=list)
    max_workers: int = 1
    executor: str = "thread"
    fail_fast: bool = False
    output_dir: str = "out/batch"
    retest: RetestPolicy = Field(default_factory=RetestPolicy)
    aggregation: dict[str, Any] = Field(default_factory=dict)
    path: Path | None = Field(default=None, exclude=True)

    @property
    def root(self) -> Path:
        """Directory used to resolve relative session paths."""
        return self.path.parent if self.path is not None else Path.cwd()

    def resolve(self, session: str) -> str:
        """Resolve a session path relative to the batch file."""
        candidate = Path(session)
        if candidate.is_absolute():
            return str(candidate)
        joined = (self.root / candidate)
        if joined.suffix in ("", ".yaml", ".yml"):
            for name in (joined, joined.with_suffix(".yaml"), joined.with_suffix(".yml")):
                if name.is_file():
                    return str(name)
        return str(joined)


_SLUG_RE = re.compile(r"[^A-Za-z0-9._+-]+")


def _normalize_keys(value: Any) -> Any:
    """Turn YAML 1.1 boolean keys back into strings (``on:`` loads as ``True``)."""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if key is True:
                key = "on"
            elif key is False:
                key = "off"
            out[key] = _normalize_keys(item)
        return out
    if isinstance(value, list):
        return [_normalize_keys(item) for item in value]
    return value


def _slug(value: Any) -> str:
    text = str(value).strip()
    text = text.replace(" ", "")
    text = _SLUG_RE.sub("-", text)
    return text or "x"


@dataclass
class _Axes:
    """Helper computing the cartesian product of the sweep axes."""

    names: list[str] = field(default_factory=list)
    values: list[list[Any]] = field(default_factory=list)

    def combos(self) -> Iterable[tuple[Any, ...]]:
        if not self.names:
            return [()]
        return itertools.product(*self.values)


def expand_jobs(spec: BatchSpec) -> list[JobSpec]:
    """Expand explicit jobs plus every sweep combination into concrete jobs."""
    sessions = list(spec.sessions)
    if spec.session:
        sessions.insert(0, spec.session)
    if not sessions and spec.jobs:
        sessions = []  # jobs carry their own session
    if not sessions and not spec.jobs:
        raise BatchSpecError("batch defines neither 'session' nor 'jobs'")

    axes = _Axes(names=list(spec.sweep), values=[list(v) for v in spec.sweep.values()])
    jobs: list[JobSpec] = []
    index = 0
    for session in sessions or [None]:
        for combo in axes.combos():
            index += 1
            overrides = dict(spec.overrides)
            labels: dict[str, str] = {}
            for axis, value in zip(axes.names, combo):
                overrides[axis] = value
                labels[axis] = str(value)
            slug = "_".join(_slug(value) for value in combo)
            job_id = f"job{index:03d}" + (f"_{slug}" if slug else "")
            jobs.append(JobSpec(
                id=job_id,
                session=spec.resolve(session) if session else "",
                overrides=overrides,
                labels=labels,
            ))
    for job in spec.jobs:
        item = job.model_copy(deep=True)
        item.session = spec.resolve(item.session)
        if item.id in {existing.id for existing in jobs}:
            raise BatchSpecError(f"duplicate job id '{item.id}'")
        jobs.append(item)
    if not jobs:  # pragma: no cover - guarded above
        raise BatchSpecError("batch expands to zero jobs")
    for job in jobs:
        if not job.session:
            raise BatchSpecError(f"job '{job.id}' has no session")
    return jobs


def load_batch(path: str | Path) -> tuple[BatchSpec, list[JobSpec]]:
    """Load and expand a ``batch.yaml`` file."""
    file_path = Path(path)
    if not file_path.is_file():
        raise BatchSpecError(f"batch file not found: {file_path}")
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - depends on user input
        raise BatchSpecError(f"{file_path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise BatchSpecError(f"{file_path}: batch file must contain a mapping")
    raw = _normalize_keys(raw)
    try:
        spec = BatchSpec.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - pydantic message is descriptive
        raise BatchSpecError(f"{file_path}: {exc}") from exc
    spec.path = file_path.resolve()
    if spec.max_workers < 1:
        raise BatchSpecError("max_workers must be >= 1")
    if spec.executor not in ("thread", "process", "serial"):
        raise BatchSpecError("executor must be one of 'thread', 'process', 'serial'")
    return spec, expand_jobs(spec)
