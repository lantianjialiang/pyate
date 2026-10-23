"""Statistics sink: per-test capability (Cpk) and bin pareto collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..engine.context import PartResult
from .result import Capability, CpkAccumulator, bin_pareto

__all__ = ["StatsSink", "collect_capability"]


def collect_capability(parts: list[PartResult]) -> list[Capability]:
    """Compute capability for every PTR test of a list of parts."""
    acc = CpkAccumulator()
    for part in parts:
        for result in part.results:
            if result.kind != "PTR" or result.skipped:
                continue
            acc.add(result.test_num, result.value, result.test_name,
                    result.lo_limit, result.hi_limit)
    return acc.report()


class StatsSink:
    """Accumulates capability + bin data and writes ``stats.json`` at run end.

    It only implements the ``on_part`` / ``on_unit_end`` / ``on_run_end`` hooks so
    it can be combined with any other datalog sink.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.acc = CpkAccumulator()
        self.bin_counts: dict[int, int] = {}
        self.parts = 0
        self.passed = 0
        self.retests = 0
        self.unit_bins: dict[str, dict[int, int]] = {}
        self._unit: str = ""

    # ------------------------------------------------------------------ hooks
    def on_unit_start(self, unit: Any) -> None:
        """Track the current unit id."""
        self._unit = getattr(unit, "unit_id", "")
        self.unit_bins.setdefault(self._unit, {})

    def on_part(self, part: PartResult, unit: Any = None) -> None:
        """Collect one part's measurements and bin."""
        if part.skipped:
            return
        self.parts += 1
        if part.passed:
            self.passed += 1
        if part.retest:
            self.retests += 1
        self.bin_counts[int(part.soft_bin)] = self.bin_counts.get(int(part.soft_bin), 0) + 1
        key = getattr(unit, "unit_id", self._unit)
        bucket = self.unit_bins.setdefault(key, {})
        bucket[int(part.soft_bin)] = bucket.get(int(part.soft_bin), 0) + 1
        for result in part.results:
            if result.kind != "PTR" or result.skipped:
                continue
            self.acc.add(result.test_num, result.value, result.test_name,
                         result.lo_limit, result.hi_limit)

    def on_run_end(self, summary: Any) -> None:
        """Write the JSON report (when a path was given)."""
        if self.path is not None:
            self.write(self.path)

    # -------------------------------------------------------------------- api
    @property
    def yield_pct(self) -> float:
        """Yield of everything collected so far."""
        return 100.0 * self.passed / self.parts if self.parts else 0.0

    def report(self) -> dict[str, Any]:
        """JSON friendly statistics."""
        return {
            "parts": self.parts,
            "passed": self.passed,
            "yield_pct": round(self.yield_pct, 3),
            "retests": self.retests,
            "bin_counts": {str(k): v for k, v in sorted(self.bin_counts.items())},
            "bin_pareto": bin_pareto(self.bin_counts),
            "unit_bins": {k: {str(b): c for b, c in sorted(v.items())}
                          for k, v in sorted(self.unit_bins.items())},
            "capability": [cap.to_dict() for cap in self.acc.report()],
        }

    def write(self, path: str | Path | None = None) -> Path:
        """Serialise the report to ``path`` (defaults to the configured path)."""
        target = Path(path or self.path or "stats.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.report(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target
