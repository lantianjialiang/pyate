"""Cross-job aggregation: yield report, bin pareto and pooled capability."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..datalog.result import bin_pareto

__all__ = ["aggregate_report", "merge_capability", "load_capability"]


def load_capability(stats_path: str | Path | None) -> list[dict[str, Any]]:
    """Read the capability block of a job's ``stats.json`` (empty when missing)."""
    if not stats_path:
        return []
    path = Path(stats_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover - corrupt output
        return []
    rows = data.get("capability")
    return list(rows) if isinstance(rows, list) else []


def merge_capability(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pool capability rows coming from different jobs (per ``test_num``).

    The pooled mean / sigma are computed from the per-job ``count``, ``mean`` and
    ``sigma`` using the parallel-variance identity:
    ``SS = sum((n_i-1)*s_i^2 + n_i*(m_i-m)^2)``.
    """
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        count = int(row.get("count") or 0)
        if count <= 0:
            continue
        buckets.setdefault(int(row["test_num"]), []).append(row)
    merged: list[dict[str, Any]] = []
    for test_num, group in sorted(buckets.items()):
        total = sum(int(row["count"]) for row in group)
        mean = sum(float(row["mean"]) * int(row["count"]) for row in group) / total
        ss = 0.0
        for row in group:
            count = int(row["count"])
            sigma = float(row.get("sigma") or 0.0)
            ss += (count - 1) * sigma * sigma + count * (float(row["mean"]) - mean) ** 2
        sigma = math.sqrt(ss / (total - 1)) if total > 1 else 0.0
        lo_values = [row.get("lo") for row in group if row.get("lo") is not None]
        hi_values = [row.get("hi") for row in group if row.get("hi") is not None]
        lo = min(lo_values) if lo_values else None
        hi = max(hi_values) if hi_values else None
        cp = (hi - lo) / (6.0 * sigma) if (sigma > 0 and lo is not None and hi is not None) else 0.0
        candidates = []
        if sigma > 0:
            if lo is not None:
                candidates.append((mean - lo) / (3.0 * sigma))
            if hi is not None:
                candidates.append((hi - mean) / (3.0 * sigma))
        merged.append({
            "test_num": test_num,
            "name": group[0].get("name", ""),
            "count": total,
            "mean": mean,
            "sigma": sigma,
            "min": min(float(row.get("min", mean)) for row in group),
            "max": max(float(row.get("max", mean)) for row in group),
            "lo": lo,
            "hi": hi,
            "cp": round(cp, 4),
            "cpk": round(min(candidates), 4) if candidates else 0.0,
        })
    return merged


def aggregate_report(jobs: Sequence[Any]) -> dict[str, Any]:
    """Aggregate a sequence of :class:`~pyate.batch.runner.JobResult` objects."""
    tested = sum(int(job.tested) for job in jobs)
    passed = sum(int(job.passed) for job in jobs)
    bin_counts: dict[int, int] = {}
    uph_values: list[float] = []
    capability_rows: list[dict[str, Any]] = []
    for job in jobs:
        for key, value in (job.bin_counts or {}).items():
            bin_counts[int(key)] = bin_counts.get(int(key), 0) + int(value)
        if job.uph:
            uph_values.append(float(job.uph))
        capability_rows.extend(load_capability(getattr(job, "stats_path", "")))
    return {
        "jobs": len(jobs),
        "done": sum(1 for job in jobs if str(getattr(job, "status", "")) == "DONE"),
        "failed": sum(1 for job in jobs if str(getattr(job, "status", "")) in ("FAILED", "ABORTED")),
        "tested": tested,
        "passed": passed,
        "yield_pct": round(100.0 * passed / tested, 3) if tested else 0.0,
        "uph_avg": round(sum(uph_values) / len(uph_values), 2) if uph_values else 0.0,
        "retests": sum(int(getattr(job, "retests", 0) or 0) for job in jobs),
        "bin_counts": {str(k): v for k, v in sorted(bin_counts.items())},
        "bin_pareto": bin_pareto(bin_counts),
        "stdf_files": sum(len(getattr(job, "stdf_files", []) or []) for job in jobs),
        "capability": merge_capability(capability_rows),
    }
