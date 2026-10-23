"""Line coverage report built without third party packages.

The target environment is offline (no ``coverage``/``pytest-cov`` wheel), so this
tool measures coverage with a small :func:`sys.settrace` hook of its own. A
custom hook is used instead of :mod:`trace` because the stdlib module drops the
counts of modules that are imported very early in some pytest runs.

Usage::

    python tools/coverage_std.py                    # full suite, gate at 85 %
    python tools/coverage_std.py --fail-under 0     # just print the table
    python tools/coverage_std.py -- -q tests/unit   # forward args to pytest
    python tools/coverage_std.py --counts out/.coverage_counts.json   # reuse
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import trace as trace_mod
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class LineCounter:
    """Records the ``(file, line)`` pairs executed inside ``prefix``."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.hits: dict[tuple[str, int], int] = {}

    def _count(self, filename: str, lineno: int) -> None:
        key = (filename, lineno)
        self.hits[key] = self.hits.get(key, 0) + 1

    def _global(self, frame: FrameType, event: str, arg: Any) -> Any:
        """Called for every new frame: only trace frames of interest."""
        filename = frame.f_code.co_filename
        if not filename.startswith(self.prefix):
            return None
        if event == "call":
            self._count(filename, frame.f_lineno)
            return self._local
        return None

    def _local(self, frame: FrameType, event: str, arg: Any) -> Any:
        if event == "line":
            self._count(frame.f_code.co_filename, frame.f_lineno)
        return self._local

    def __call__(self, frame: FrameType, event: str, arg: Any) -> Any:
        return self._global(frame, event, arg)


def _executable_lines(path: Path) -> set[int]:
    """Line numbers that actually execute (``trace``'s own definition)."""
    try:
        table = trace_mod._find_executable_linenos(str(path))  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        return set()
    return {int(line) for line in table if isinstance(line, int) and line > 0}


def _source_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _normalise(counts: dict[tuple[str, int], int]) -> dict[tuple[str, int], int]:
    """Make path keys comparable (case and separators differ between runs)."""
    out: dict[tuple[str, int], int] = {}
    for (path, line), value in counts.items():
        try:
            key = str(Path(path).resolve())
        except OSError:  # pragma: no cover - defensive
            key = str(path)
        out[(key, int(line))] = int(value)
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the suite (or reuse counts) and print a per-module coverage table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-under", type=float, default=85.0)
    parser.add_argument("--base", default=str(SRC / "pyate"))
    parser.add_argument("--show", type=int, default=15, help="how many rows to print")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save-counts", type=Path, default=None,
                        help="write the raw hit counts so later runs can skip pytest")
    parser.add_argument("--counts", type=Path, default=None,
                        help="reuse hit counts from an earlier --save-counts run")
    parser.add_argument("pytest_args", nargs="*", help="arguments forwarded to pytest")
    args = parser.parse_args(argv)

    base = Path(args.base).resolve()
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    exit_code = 0
    if args.counts is not None:
        raw = json.loads(args.counts.read_text(encoding="utf-8"))
        # Windows paths contain a drive colon, so split from the right
        loaded: dict[tuple[str, int], int] = {}
        for key, value in raw.items():
            path, _, line = key.rpartition(":")
            loaded[(path, int(line))] = int(value)
        counts = _normalise(loaded)
    else:
        import pytest

        counter = LineCounter(str(base))
        previous = sys.gettrace()
        sys.settrace(counter)
        threading.settrace(counter)
        try:
            exit_code = int(pytest.main(args.pytest_args or ["-q"]))
        finally:
            sys.settrace(previous)
            threading.settrace(None)  # type: ignore[arg-type]
        counts = _normalise(counter.hits)
    if args.save_counts is not None:
        args.save_counts.parent.mkdir(parents=True, exist_ok=True)
        payload = {f"{path}:{line}": value for (path, line), value in counts.items()}
        args.save_counts.write_text(json.dumps(payload), encoding="utf-8")

    rows: list[tuple[str, int, int, float]] = []
    total_exec = total_hit = 0
    for path in _source_files(base):
        lines = _executable_lines(path)
        if not lines:
            continue
        resolved = str(path.resolve())
        hit = {line for line in lines if (resolved, line) in counts}
        total_exec += len(lines)
        total_hit += len(hit)
        rows.append((str(path.relative_to(base)), len(lines), len(hit),
                     100.0 * len(hit) / len(lines)))

    rows.sort(key=lambda row: row[3])
    pct = 100.0 * total_hit / total_exec if total_exec else 0.0
    if args.json:
        print(json.dumps({"percent": round(pct, 2), "hit": total_hit, "total": total_exec,
                          "modules": [{"module": m, "statements": s, "covered": c,
                                       "pct": round(p, 2)} for m, s, c, p in rows]},
                         indent=2))
    else:
        print(f"{'module':50s} {'stmts':>6s} {'cover':>6s} {'pct':>7s}")
        for module, statements, covered, module_pct in rows[: args.show]:
            print(f"{module:50s} {statements:6d} {covered:6d} {module_pct:6.1f}%")
        print(f"{'TOTAL':50s} {total_exec:6d} {total_hit:6d} {pct:6.1f}%")
    return 0 if (exit_code == 0 and pct >= args.fail_under) else 1


if __name__ == "__main__":
    raise SystemExit(main())
