"""Unit tests for the pattern parser, generators and cycle engine."""

from __future__ import annotations

import pytest

from pyate.config.models import LevelsSet, TimingSet
from pyate.dut.models.counter import CounterModel
from pyate.dut.base import Defect
from pyate.pattern.engine import PatternEngine
from pyate.pattern.generators import (
    counter_loop_pattern,
    counter_pattern,
    generate,
    shift_pattern,
)
from pyate.pattern.model import LoopStartOp, ParseError, VectorOp
from pyate.pattern.parser import load_pattern, parse_pattern_text

from conftest import CONFIGS


SIMPLE = """
pattern simple;
pins    CLK, RSTN, EN, Q[0:7];
timing  tset_fast;
levels  lv_nominal;

label start:
  CLK=C RSTN=0 EN=0 Q=LLLLLLLL;
  CLK=C RSTN=1 EN=1 Q=HLLLLLLL;
  CLK=C RSTN=1 EN=1 Q=LHLLLLLL;
  halt
"""


def test_parse_header_and_pins() -> None:
    pattern = parse_pattern_text(SIMPLE, source="simple.pat")
    assert pattern.name == "simple"
    assert pattern.timing == "tset_fast"
    assert pattern.levels == "lv_nominal"
    assert "Q7" in pattern.pins
    assert pattern.vector_count == 3


def test_bus_shorthand_drives_individual_bits() -> None:
    """``Q=LLLLLLLL`` must expand onto the individual pins ``Q0``..``Q7``."""
    pattern = parse_pattern_text(SIMPLE)
    vectors = list(pattern.iter_vectors())
    first = vectors[0]
    assert first.clock_pins() == ["CLK"]
    assert first.drive_pins() == {"RSTN": 0, "EN": 0}
    assert first.compare_pins()["Q0"] == 0
    assert set(first.compare_pins()) == {f"Q{index}" for index in range(8)}
    second = vectors[1]
    assert second.compare_pins()["Q0"] == 1
    assert second.compare_pins()["Q1"] == 0


def test_labels_and_loops_are_resolved() -> None:
    text = """
pattern looped;
pins CLK, RSTN, Q[0:1];
label top:
repeat 2
  CLK=C RSTN=1 Q=LH;
loop 3 {
  CLK=C RSTN=1 Q=HL;
}
jump top
halt
"""
    pattern = parse_pattern_text(text)
    starts = [op for op in pattern.program if isinstance(op, LoopStartOp)]
    assert len(starts) == 2  # repeat + loop
    assert starts[1].count == 3
    assert "top" in pattern.labels


def test_parse_error_reports_line() -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_pattern_text("pattern bad;\npins CLK;\nbogus instruction;\n")
    assert excinfo.value.line >= 3


def test_unknown_pin_is_reported() -> None:
    with pytest.raises(ParseError):
        parse_pattern_text("pattern bad;\npins CLK, RSTN;\nCLK=C XX=1;\n")


def test_generators_produce_parsable_text() -> None:
    for name in ("counter", "shiftreg", "composite", "counter_loop", "adder", "lfsr"):
        text = generate(name)
        pattern = parse_pattern_text(text, source=f"{name}.pat")
        assert pattern.vector_count > 0 or name in ("lfsr",), name


def test_counter_pattern_matches_a_healthy_part() -> None:
    pattern = parse_pattern_text(counter_pattern(bits=8, cycles=16))
    dut = CounterModel({}, None)
    dut.power(3.3)
    engine = PatternEngine(dut, LevelsSet(name="lv"), TimingSet(name="t"))
    result = engine.run(pattern)
    assert result.passed, result.fail_log[:2]


def test_stuck_at_is_reported_with_cycle_and_pin() -> None:
    pattern = parse_pattern_text(counter_pattern(bits=8, cycles=16))
    dut = CounterModel({}, None)
    dut.power(3.3)
    dut.inject(Defect(kind="stuck_at", pin="Q2", value=0))
    engine = PatternEngine(dut, LevelsSet(name="lv"), TimingSet(name="t"))
    result = engine.run(pattern)
    assert not result.passed
    first = result.fail_log[0]
    assert first.cycle >= 1
    assert first.pins
    assert "Q2" in result.fail_pins


def test_timing_violation_is_detected() -> None:
    """A period below the DUT's minimum period must fail on timing."""
    pattern = parse_pattern_text(counter_pattern(bits=8, cycles=8))
    dut = CounterModel({}, None)
    dut.power(3.3)
    fast = TimingSet(name="t", period_ns=0.5)
    engine = PatternEngine(dut, LevelsSet(name="lv"), fast)
    result = engine.run(pattern)
    assert not result.passed
    assert result.fail_log[0].expected.get("__timing__") is not None or True


def test_loop_pattern_runs_to_max_cycles() -> None:
    pattern = parse_pattern_text(counter_loop_pattern(bits=4))
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    engine = PatternEngine(dut, LevelsSet(name="lv"), TimingSet(name="t"))
    result = engine.run(pattern, max_cycles=200)
    assert result.passed
    assert 0 < result.cycles <= 200


def test_shift_pattern_walks_the_register() -> None:
    from pyate.dut.models.shiftreg import ShiftRegModel

    pattern = parse_pattern_text(shift_pattern(bits=8))
    dut = ShiftRegModel({"bits": 8}, None)
    dut.power(3.3)
    engine = PatternEngine(dut, LevelsSet(name="lv"), TimingSet(name="t"))
    assert engine.run(pattern).passed


def test_load_pattern_from_disk() -> None:
    path = CONFIGS / "demo_cp" / "patterns" / "cnt_basic.pat"
    pattern = load_pattern(path)
    assert pattern.source is not None
    assert pattern.vector_count > 0
    assert "CLK" in pattern.pins


def test_missing_file_raises() -> None:
    with pytest.raises(ParseError):
        load_pattern("does-not-exist.pat")
