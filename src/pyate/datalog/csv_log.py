"""CSV and human readable datalog sinks."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, TextIO

from ..engine.context import PartResult
from .engine_sink import DatalogSink

__all__ = ["CsvSink", "TextDatalogSink"]


class CsvSink(DatalogSink):
    """Writes one CSV per unit with one row per test result."""

    HEADER = [
        "unit", "part_id", "site", "x", "y", "soft_bin", "hard_bin", "part_passed",
        "test_num", "test_name", "kind", "value", "unit_name", "lo_limit", "hi_limit",
        "passed", "pin", "fail_code", "cycle_cnt", "num_fail",
    ]

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO | None = None
        self._writer: Any = None
        self._unit = ""
        self.files: list[Path] = []

    def on_unit_start(self, unit: Any) -> None:
        """Open the CSV for this unit."""
        self._unit = unit.unit_id
        path = self.output_dir / f"{unit.unit_id}_datalog.csv"
        self._handle = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(self.HEADER)
        self.files.append(path)

    def on_part(self, part: PartResult, unit: Any) -> None:
        """Write one row per result."""
        if self._writer is None or part.skipped:
            return
        rows = part.results or []
        if not rows:
            self._writer.writerow([
                unit.unit_id, part.part_id, part.site, part.x, part.y, part.soft_bin,
                part.hard_bin, part.passed, "", "", "", "", "", "", "", "", "", "", "", "",
            ])
            return
        for result in rows:
            if result.skipped:
                continue
            self._writer.writerow([
                unit.unit_id, part.part_id, part.site, part.x, part.y, part.soft_bin,
                part.hard_bin, part.passed, result.test_num, result.test_name, result.kind,
                "" if result.value is None else f"{result.value:.9g}", result.unit,
                "" if result.lo_limit is None else f"{result.lo_limit:.9g}",
                "" if result.hi_limit is None else f"{result.hi_limit:.9g}",
                result.passed, result.pin or "", result.fail_code, result.cycle_cnt,
                result.num_fail,
            ])

    def on_unit_end(self, summary: Any) -> None:
        """Close the CSV."""
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None
            self._writer = None

    def close(self) -> None:
        """Close a partially written CSV."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class TextDatalogSink(DatalogSink):
    """ATE style human readable datalog (one block per part)."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO | None = None
        self._unit = ""
        self.files: list[Path] = []

    def on_unit_start(self, unit: Any) -> None:
        """Open the datalog file for this unit."""
        self._unit = unit.unit_id
        path = self.output_dir / f"{unit.unit_id}_datalog.txt"
        self._handle = path.open("w", encoding="utf-8")
        self._handle.write(f"# PYATE datalog  unit={unit.unit_id}  kind={unit.kind}\n")
        self._handle.write("# " + "=" * 78 + "\n")
        self.files.append(path)

    def on_part(self, part: PartResult, unit: Any) -> None:
        """Write the part block."""
        if self._handle is None or part.skipped:
            return
        status = "PASS" if part.passed else "FAIL"
        self._handle.write(
            f"\nPART {part.part_id}  SITE {part.site}  X={part.x} Y={part.y}  "
            f"BIN {part.soft_bin}/{part.hard_bin}  {status}  {part.test_time_ms:.1f} ms\n"
        )
        for result in part.results:
            if result.skipped:
                self._handle.write(f"  {result.test_num:6d} {result.test_name:26s} SKIPPED\n")
                continue
            value = "  ---  " if result.value is None else f"{result.value:10.5g}"
            limits = ""
            if result.lo_limit is not None or result.hi_limit is not None:
                low = "-inf" if result.lo_limit is None else f"{result.lo_limit:g}"
                high = "+inf" if result.hi_limit is None else f"{result.hi_limit:g}"
                limits = f" [{low},{high}]"
            flag = "PASS" if result.passed else f"FAIL({result.fail_code})"
            pin = f" {result.pin}" if result.pin else ""
            self._handle.write(
                f"  {result.test_num:6d} {result.test_name:26s}{pin:>6s} "
                f"{value} {result.unit:3s}{limits:20s} {flag}\n"
            )

    def on_unit_end(self, summary: Any) -> None:
        """Write the unit footer and close."""
        if self._handle is not None:
            self._handle.write(
                f"\n# unit {summary.unit_id}: tested={summary.tested} passed={summary.passed} "
                f"yield={summary.yield_pct:.2f}% uph={summary.uph:.1f}\n"
            )
            self._handle.close()
            self._handle = None

    def close(self) -> None:
        """Close a partially written datalog."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
