"""Pattern data model: vectors, control-flow instructions and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Union

__all__ = [
    "Vector", "VectorOp", "JumpOp", "LoopStartOp", "LoopEndOp", "HaltOp", "LabelOp",
    "Instruction", "Pattern", "FailCycle", "PatternResult", "ParseError",
]


class ParseError(Exception):
    """Raised for syntax errors; the message always carries the line number."""

    def __init__(self, message: str, line: int | None = None, source: str | None = None) -> None:
        self.line = line
        self.source = source
        where = f"{source or 'pattern'}:{line}" if line else (source or "pattern")
        super().__init__(f"{where}: {message}")


@dataclass(frozen=True)
class Vector:
    """One pin-state vector (states already bus-expanded)."""

    states: dict[str, str]
    tset: str | None = None
    line: int = 0
    label: str | None = None

    def drive_pins(self) -> dict[str, int]:
        """Pins driven with 0/1 (``C`` is handled by the engine)."""
        return {pin: (1 if state in ("1", "H") else 0)
                for pin, state in self.states.items() if state in ("0", "1")}

    def compare_pins(self) -> dict[str, int]:
        """Pins with an expected logic level (``L``/``H``)."""
        return {pin: (1 if state == "H" else 0)
                for pin, state in self.states.items() if state in ("L", "H")}

    def clock_pins(self) -> list[str]:
        """Pins pulsed as a clock in this vector."""
        return [pin for pin, state in self.states.items() if state == "C"]


@dataclass(frozen=True)
class VectorOp:
    """Execute one vector."""

    vector: Vector


@dataclass(frozen=True)
class JumpOp:
    """Unconditional jump to a label."""

    target: str


@dataclass(frozen=True)
class LoopStartOp:
    """Begin a counted loop (``end_pc`` patched after parsing)."""

    count: int
    end_pc: int = -1


@dataclass(frozen=True)
class LoopEndOp:
    """End of a counted loop; jumps back to its ``LoopStartOp``."""

    start_pc: int


@dataclass(frozen=True)
class HaltOp:
    """Stop execution."""


@dataclass(frozen=True)
class LabelOp:
    """A no-op instruction marking a jump target."""

    name: str


Instruction = Union[VectorOp, JumpOp, LoopStartOp, LoopEndOp, HaltOp, LabelOp]


@dataclass
class Pattern:
    """A parsed pattern: pins, metadata and a flat instruction list."""

    name: str
    pins: list[str] = field(default_factory=list)
    program: list[Instruction] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    timing: str | None = None
    levels: str | None = None
    source: str | None = None
    includes: list[str] = field(default_factory=list)

    def header(self) -> dict[str, Any]:
        """Parsed header, used by the config validator."""
        return {
            "name": self.name,
            "pins": list(self.pins),
            "timing": self.timing,
            "levels": self.levels,
        }

    @property
    def vector_count(self) -> int:
        """Number of vector instructions (static count, loops collapsed)."""
        return sum(1 for op in self.program if isinstance(op, VectorOp))

    def iter_vectors(self) -> Iterable[Vector]:
        """Iterate the static vector list."""
        for op in self.program:
            if isinstance(op, VectorOp):
                yield op.vector

    def to_dict(self) -> dict[str, Any]:
        """Summary used by the GUI pattern browser."""
        return {
            "name": self.name,
            "pins": list(self.pins),
            "instructions": len(self.program),
            "vectors": self.vector_count,
            "labels": dict(self.labels),
            "timing": self.timing,
            "levels": self.levels,
            "source": self.source,
        }


@dataclass(frozen=True)
class FailCycle:
    """One failing cycle (maps onto a STDF FTR)."""

    cycle: int
    vector_index: int
    line: int
    pins: tuple[str, ...]
    expected: dict[str, int] = field(default_factory=dict)
    actual: dict[str, int | None] = field(default_factory=dict)


@dataclass
class PatternResult:
    """Outcome of a pattern run."""

    passed: bool = True
    cycles: int = 0
    fail_count: int = 0
    first_fail_cycle: int | None = None
    fail_pins: tuple[str, ...] = ()
    fail_log: list[FailCycle] = field(default_factory=list)
    captures: dict[str, list[int]] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    stopped_at: str | None = None
    peak_fail_pins: tuple[str, ...] = ()

    def merge_fail_pins(self) -> None:
        """Recompute ``fail_pins`` from the fail log."""
        pins: set[str] = set()
        for entry in self.fail_log:
            pins.update(entry.pins)
        self.fail_pins = tuple(sorted(pins))

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary."""
        return {
            "passed": self.passed,
            "cycles": self.cycles,
            "fail_count": self.fail_count,
            "first_fail_cycle": self.first_fail_cycle,
            "fail_pins": list(self.fail_pins),
            "elapsed_ms": self.elapsed_ms,
            "fail_log": [
                {"cycle": f.cycle, "vector_index": f.vector_index, "line": f.line,
                 "pins": list(f.pins)}
                for f in self.fail_log
            ],
        }
