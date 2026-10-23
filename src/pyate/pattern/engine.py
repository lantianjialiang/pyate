"""Cycle based pattern execution and compare."""

from __future__ import annotations

import random
from typing import Mapping

from ..config.models import LevelsSet, TimingSet
from ..dut.base import DUTModel
from ..hal.timing import TimingPlan
from .model import (
    FailCycle,
    HaltOp,
    JumpOp,
    LabelOp,
    LoopEndOp,
    LoopStartOp,
    Pattern,
    PatternResult,
    Vector,
    VectorOp,
)

__all__ = ["PatternEngine"]


class PatternEngine:
    """Executes a :class:`~pyate.pattern.model.Pattern` against a DUT model."""

    def __init__(
        self,
        dut: DUTModel,
        levels: LevelsSet,
        timing: TimingSet,
        *,
        timings: Mapping[str, TimingSet] | None = None,
        rng: random.Random | None = None,
        noise_sigma: float = 0.0,
        max_fail_log: int = 1000,
        max_cycles: int = 1_000_000,
    ) -> None:
        self.dut = dut
        self.levels = levels
        self.timing = timing
        self.timings = dict(timings or {})
        self.timings.setdefault(timing.name, timing)
        self.rng = rng or random.Random(0)
        self.noise_sigma = float(noise_sigma)
        self.max_fail_log = int(max_fail_log)
        self.max_cycles = int(max_cycles)

    # ------------------------------------------------------------------ util
    def _plan(self, vector: Vector) -> TimingPlan:
        timing = self.timings.get(vector.tset or "", self.timing)
        return TimingPlan.from_set(timing)

    def _apply_vector(self, vector: Vector) -> dict[str, int | None]:
        """Drive the vector and return the DUT outputs at the compare strobe."""
        clocks = vector.clock_pins()
        inputs = vector.drive_pins()
        if clocks:
            clock = clocks[0]
            self.dut.drive(clock, 0)
            self.dut.set_inputs(inputs)
            self.dut.drive(clock, 1)
            outputs = self.dut.clock_cycle()
            self.dut.drive(clock, 0)
            return outputs
        return self.dut.evaluate(inputs)

    # ------------------------------------------------------------------- run
    def run(
        self,
        pattern: Pattern,
        *,
        start_label: str | None = None,
        stop_on_first_fail: bool = True,
        max_cycles: int | None = None,
        capture_start: int | None = None,
        capture_stop: int | None = None,
        capture_pins: list[str] | None = None,
    ) -> PatternResult:
        """Execute ``pattern`` and collect fail information."""
        program = pattern.program
        labels = pattern.labels
        limit = int(max_cycles or self.max_cycles)
        result = PatternResult()
        counters: dict[int, int] = {}
        captures: dict[str, list[int]] = {pin: [] for pin in (capture_pins or [])}
        cycles = 0
        vector_address = 0
        elapsed_ns = 0.0
        pc = labels.get(start_label, 0) if start_label else 0
        if start_label and start_label not in labels:
            raise KeyError(f"pattern '{pattern.name}' has no label '{start_label}'")
        total_fail = 0

        while pc < len(program):
            op = program[pc]
            if isinstance(op, HaltOp):
                result.stopped_at = "halt"
                break
            if isinstance(op, LabelOp):
                pc += 1
                continue
            if isinstance(op, JumpOp):
                pc = labels[op.target]
                continue
            if isinstance(op, LoopStartOp):
                remaining = counters.get(pc, op.count)
                if remaining <= 0:
                    counters.pop(pc, None)
                    pc = op.end_pc if op.end_pc > 0 else len(program)
                else:
                    counters[pc] = remaining - 1
                    pc += 1
                continue
            if isinstance(op, LoopEndOp):
                pc = op.start_pc
                continue
            if not isinstance(op, VectorOp):  # pragma: no cover - defensive
                pc += 1
                continue

            if cycles >= limit:
                result.stopped_at = "max_cycles"
                break
            vector = op.vector
            cycles += 1
            vector_address += 1
            plan = self._plan(vector)
            elapsed_ns += plan.period_ns
            outputs = self._apply_vector(vector)

            expected = vector.compare_pins()
            if expected:
                failing: list[str] = []
                actual: dict[str, int | None] = {}
                min_period = self.dut.min_period_ns(self.dut.vdd)
                timing_violation = plan.period_ns < min_period
                for pin, want in expected.items():
                    got = outputs.get(pin)
                    actual[pin] = got
                    if timing_violation or got is None or got != want:
                        failing.append(pin)
                if not failing and timing_violation:  # pragma: no cover - covered above
                    failing = list(expected)
                for pin in captures:
                    value = outputs.get(pin)
                    captures[pin].append(-1 if value is None else int(value))
                if failing:
                    total_fail += len(failing)
                    if result.first_fail_cycle is None:
                        result.first_fail_cycle = cycles
                        result.peak_fail_pins = tuple(sorted(failing))
                    if len(result.fail_log) < self.max_fail_log:
                        result.fail_log.append(
                            FailCycle(
                                cycle=cycles,
                                vector_index=pc,
                                line=vector.line,
                                pins=tuple(sorted(failing)),
                                expected={pin: expected[pin] for pin in failing},
                                actual={pin: actual[pin] for pin in failing},
                            )
                        )
                    if stop_on_first_fail:
                        result.stopped_at = "fail"
                        break
            pc += 1

        result.cycles = cycles
        result.fail_count = total_fail
        result.passed = total_fail == 0
        result.elapsed_ms = elapsed_ns / 1e6
        result.captures = captures
        result.merge_fail_pins()
        if not result.fail_pins and result.peak_fail_pins:
            result.fail_pins = result.peak_fail_pins
        return result

    # --------------------------------------------------------------- helpers
    def measure_outputs(self, vector: Vector) -> dict[str, int | None]:
        """Drive one vector without comparing (used by debug tooling)."""
        return self._apply_vector(vector)
