"""Pattern debugging facade (used by ``pyate pattern run`` and the GUI panel).

Runs one ``.pat`` file against a DUT model without needing a full session, which
is what a test engineer does when a vector set misbehaves: isolate the pattern,
then go back to the flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.models import DutProfile, LevelsSet, TimingSet, VddSpec
from ..dut.registry import create_dut, list_models
from ..pattern.engine import PatternEngine
from ..pattern.model import FailCycle, PatternResult
from ..pattern.parser import load_pattern

__all__ = ["PatternRunReport", "available_models", "default_profile", "run_pattern"]


@dataclass
class PatternRunReport:
    """Everything a caller needs to display a standalone pattern run."""

    path: str
    pattern: str
    vectors: int
    cycles: int
    passed: bool
    elapsed_ms: float
    fail_count: int
    fail_pins: tuple[str, ...]
    first_fail: FailCycle | None = None
    result: PatternResult | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view (the raw result is omitted)."""
        return {
            "path": self.path, "pattern": self.pattern, "vectors": self.vectors,
            "cycles": self.cycles, "passed": self.passed,
            "elapsed_ms": round(self.elapsed_ms, 3), "fail_count": self.fail_count,
            "fail_pins": list(self.fail_pins),
            "first_fail": None if self.first_fail is None else {
                "cycle": self.first_fail.cycle, "line": self.first_fail.line,
                "pins": list(self.first_fail.pins),
                "expected": dict(self.first_fail.expected),
                "actual": dict(self.first_fail.actual),
            },
        }


def available_models() -> list[str]:
    """DUT model names a pattern can be run against."""
    return list_models()


def default_profile(model: str, **params: Any) -> DutProfile:
    """A defect free profile so the pattern result reflects the vectors only."""
    payload: dict[str, Any] = {"model": model, "params": dict(params), "defects": [],
                               "yield": {"pass_rate": 1.0}}
    return DutProfile.model_validate(payload)


def run_pattern(
    path: str | Path,
    *,
    model: str = "composite",
    vdd: float = 3.3,
    period_ns: float = 20.0,
    max_cycles: int = 100_000,
    start_label: str | None = None,
    stop_on_first_fail: bool = True,
    seed: int = 0,
    params: dict[str, Any] | None = None,
) -> PatternRunReport:
    """Load ``path`` and execute it against a freshly created ``model``.

    Raises :class:`~pyate.pattern.parser.PatternError` for a malformed pattern and
    :class:`~pyate.dut.registry.DutError` (or ``KeyError``) for an unknown model.
    """
    pattern = load_pattern(str(path))
    profile = default_profile(model, **(params or {}))
    dut = create_dut(profile, seed=seed, sample_yield=False)
    dut.power(vdd)
    levels = LevelsSet(
        name=pattern.levels or "lv",
        vdd=VddSpec(nom=vdd, min=vdd * 0.9, max=vdd * 1.1),
    )
    timing = TimingSet(name=pattern.timing or "t", period_ns=period_ns)
    engine = PatternEngine(dut, levels, timing, max_cycles=max_cycles)
    result = engine.run(pattern, start_label=start_label, max_cycles=max_cycles,
                        stop_on_first_fail=stop_on_first_fail)
    return PatternRunReport(
        path=str(path),
        pattern=pattern.name,
        vectors=pattern.vector_count,
        cycles=result.cycles,
        passed=result.passed,
        elapsed_ms=result.elapsed_ms,
        fail_count=result.fail_count,
        fail_pins=result.fail_pins,
        first_fail=result.fail_log[0] if result.fail_log else None,
        result=result,
    )
