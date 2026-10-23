"""Unit tests for the DUT behaviour models, defects and yield sampling."""

from __future__ import annotations

import math

import pytest

from pyate.dut.base import DUTError, Defect
from pyate.dut.models.adder import AdderModel
from pyate.dut.models.composite import CompositeModel
from pyate.dut.models.counter import CounterModel
from pyate.dut.models.gates import GateArrayModel
from pyate.dut.models.lfsr import LfsrModel
from pyate.dut.models.shiftreg import ShiftRegModel
from pyate.dut.models.sram import SramModel
from pyate.dut.registry import (
    YieldSampler,
    create_dut,
    get_dut_class,
    is_registered,
    list_models,
    mix_seed,
)

from conftest import CONFIGS  # noqa: F401  (keeps the shared conftest import path)
from pyate.config.loader import load_session


def _bits(value: int, width: int, prefix: str = "B") -> dict[str, int]:
    """Spread ``value`` over individual pins ``<prefix>0..<prefix><width-1>``."""
    return {f"{prefix}{index}": (value >> index) & 1 for index in range(width)}


# --------------------------------------------------------------------- registry
def test_builtin_models_are_registered() -> None:
    models = list_models()
    for name in ("counter", "shiftreg", "adder", "lfsr", "sram", "composite", "gates"):
        assert name in models
        assert is_registered(name)


def test_unknown_model_raises_with_available_list() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_dut_class("no-such-die")
    assert "available" in str(excinfo.value)


def test_mix_seed_is_deterministic_and_coordinate_sensitive() -> None:
    assert mix_seed(7, 1.0, 2.0) == mix_seed(7, 1.0, 2.0)
    assert mix_seed(7, 1.0, 2.0) != mix_seed(7, 2.0, 1.0)
    assert mix_seed(8, 1.0, 2.0) != mix_seed(7, 1.0, 2.0)


# ---------------------------------------------------------------------- counter
def test_counter_counts_only_when_enabled() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 1, "EN": 0})
    for _ in range(3):
        dut.clock_cycle()
    assert dut.value == 0
    dut.set_inputs({"EN": 1})
    dut.clock_cycle()
    assert dut.value == 1


def test_counter_reset_is_synchronous_and_active_low() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 1, "EN": 1})
    dut.clock_cycle()
    dut.clock_cycle()
    assert dut.value == 2
    dut.set_inputs({"RSTN": 0})
    assert dut.value == 2  # not reset until the edge arrives
    dut.clock_cycle()
    assert dut.value == 0


def test_counter_wraps_at_the_bus_width() -> None:
    dut = CounterModel({"bits": 3}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 0, "EN": 1})
    dut.clock_cycle()
    dut.set_inputs({"RSTN": 1})
    for _ in range(8):
        dut.clock_cycle()
    assert dut.value == 0


def test_counter_rejects_illegal_width() -> None:
    with pytest.raises(ValueError):
        CounterModel({"bits": 0}, None)


# ------------------------------------------------------------------- shift/lfsr
def test_shift_register_shifts_towards_the_output() -> None:
    dut = ShiftRegModel({"bits": 4}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 0, "SI": 0})
    dut.clock_cycle()
    dut.set_inputs({"RSTN": 1, "SI": 1})
    dut.clock_cycle()
    assert dut.outputs()["Q0"] == 1


def test_lfsr_never_gets_stuck_at_zero() -> None:
    dut = LfsrModel({"bits": 8}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 0})
    dut.clock_cycle()
    dut.set_inputs({"RSTN": 1})
    seen = set()
    for _ in range(32):
        dut.clock_cycle()
        value = sum(int(dut.outputs()[f"Q{i}"] or 0) << i for i in range(8))
        seen.add(value)
    assert len(seen) > 8


# ----------------------------------------------------------------------- others
def test_adder_sums_with_carry() -> None:
    dut = AdderModel({"bits": 8}, None)
    dut.power(3.3)
    for a, b, cin in ((10, 20, 0), (255, 255, 1), (0, 0, 1)):
        states: dict[str, int] = {"CIN": cin}
        states.update({f"A{i}": (a >> i) & 1 for i in range(8)})
        states.update({f"B{i}": (b >> i) & 1 for i in range(8)})
        outputs = dut.evaluate(states)
        total = sum(int(outputs[f"S{i}"] or 0) << i for i in range(8))
        carry = int(outputs["COUT"] or 0)
        assert total + (carry << 8) == a + b + cin


def test_gate_array_computes_the_configured_gate() -> None:
    dut = GateArrayModel({"gate": "xor", "bits": 2}, None)
    dut.power(3.3)
    outputs = dut.evaluate({"A0": 1, "B0": 0, "A1": 1, "B1": 1})
    assert outputs["Y0"] == 1
    assert outputs["Y1"] == 0


def test_sram_write_then_read_roundtrip() -> None:
    dut = SramModel({"depth": 8, "width": 4}, None)
    dut.power(3.3)
    assert dut.addr_bits == 3
    for address in range(8):
        data = (address * 5) & 0x0F
        dut.set_inputs({"RSTN": 1, "CEN": 0, "WEN": 0,
                        **_bits(address, 3, "A"), **_bits(data, 4, "D")})
        dut.clock_cycle()
    for address in range(8):
        data = (address * 5) & 0x0F
        dut.set_inputs({"RSTN": 1, "CEN": 0, "WEN": 1, **_bits(address, 3, "A")})
        outputs = dut.clock_cycle()
        read = sum(int(outputs[f"Q{i}"] or 0) << i for i in range(4))
        assert read == data


def test_composite_runs_counter_and_shift_block() -> None:
    dut = CompositeModel({"counter_bits": 4, "shift_bits": 4}, None)
    dut.power(3.3)
    dut.set_inputs({"RSTN": 0, "EN": 0, "SI": 0})
    dut.clock_cycle()
    dut.set_inputs({"RSTN": 1, "EN": 1, "SI": 1})
    outputs = dut.clock_cycle()
    assert outputs["Q0"] == 1
    assert outputs["S0"] == 1


# ---------------------------------------------------------------- analog / power
def test_power_off_clears_outputs_and_blocks_function() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    assert dut.is_functional()
    dut.power(0.0)
    assert not dut.powered
    assert all(value is None for value in dut.outputs().values())
    assert dut.supply_current() == 0.0


def test_supply_current_grows_with_voltage_and_idd_defect() -> None:
    dut = CounterModel({}, None)
    dut.power(3.3)
    low = dut.supply_current(2.7)
    high = dut.supply_current(3.6)
    assert high > low > 0
    baseline = dut.supply_current(3.3)
    dut.inject(Defect("high_idd", factor=3.0))
    assert dut.supply_current(3.3) > baseline * 2.5


def test_negative_supply_is_rejected() -> None:
    dut = CounterModel({}, None)
    with pytest.raises(DUTError):
        dut.power(-1.0)


def test_min_period_and_max_frequency_are_consistent() -> None:
    dut = CounterModel({}, None)
    dut.power(3.3)
    period = dut.min_period_ns()
    assert period > 0
    if not math.isinf(period):
        assert dut.max_freq_mhz() == pytest.approx(1000.0 / period)
    duty = CounterModel({"vth_v": 3.3}, None)
    duty.power(3.3)
    assert math.isinf(duty.min_period_ns())


def test_slow_defect_raises_minimum_period() -> None:
    dut = CounterModel({}, None)
    dut.power(3.3)
    nominal = dut.min_period_ns()
    dut.inject(Defect("slow", factor=2.0))
    assert dut.min_period_ns() > nominal * 1.5


def test_output_voltage_reflects_logic_level() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.load(1)
    assert dut.output_voltage("Q0") == pytest.approx(3.2)
    assert dut.output_voltage("Q1") == pytest.approx(0.05)
    with pytest.raises(DUTError):
        dut.output_voltage("RSTN")


# ---------------------------------------------------------------------- defects
def test_stuck_at_output_is_forced() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.inject(Defect("stuck_at", pin="Q2", value=1))
    dut.load(0)
    assert dut.evaluate()["Q2"] == 1


def test_stuck_at_input_is_forced() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.inject(Defect("stuck_at", pin="EN", value=1))
    dut.set_inputs({"RSTN": 0, "EN": 0})
    dut.clock_cycle()
    dut.set_inputs({"RSTN": 1, "EN": 0})
    dut.clock_cycle()
    assert dut.value == 1


def test_open_pin_floats_and_reads_rail_on_continuity() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.inject(Defect("open", pin="Q0"))
    assert dut.evaluate()["Q0"] is None
    assert dut.continuity_vdiode("Q0") > 3.0


def test_short_pin_reads_near_zero_volts() -> None:
    dut = CounterModel({"bits": 4}, None)
    dut.power(3.3)
    dut.inject(Defect("short", pin="Q0", pin2="Q1"))
    assert abs(dut.continuity_vdiode("Q0")) < 0.1


def test_leakage_defect_raises_pin_current() -> None:
    dut = CounterModel({}, None)
    dut.power(3.3)
    clean = dut.pin_leakage("RSTN", 1.0)
    dut.inject(Defect("leakage", pin="RSTN", na=1000.0))
    assert dut.pin_leakage("RSTN", 1.0) > clean + 5e-7


def test_continuity_polarity_flips_sign() -> None:
    dut = CounterModel({}, None)
    dut.power(3.3)
    forward = dut.continuity_vdiode("RSTN", 1)
    reverse = dut.continuity_vdiode("RSTN", -1)
    assert forward > 0 > reverse


def test_unknown_defect_kind_and_pin_are_rejected() -> None:
    dut = CounterModel({}, None)
    with pytest.raises(DUTError):
        dut.inject(Defect("explode"))
    with pytest.raises(DUTError):
        dut.inject(Defect("stuck_at", pin="NOPE", value=0))
    with pytest.raises(DUTError):
        dut.inject(Defect("stuck_at", value=0))
    with pytest.raises(DUTError):
        dut.inject(Defect("short", pin="Q0"))
    with pytest.raises(DUTError):
        dut.inject(Defect("leakage"))


def test_defect_summary_is_human_readable() -> None:
    dut = CounterModel({}, None)
    dut.inject(Defect("stuck_at", pin="Q0", value=0))
    dut.inject(Defect("leakage", pin="EN", na=800.0))
    summary = dut.defect_summary()
    assert "stuck_at(Q0=0)" in summary
    assert any("leakage" in entry for entry in summary)


# ----------------------------------------------------------------- yield model
def test_create_dut_from_profile_is_reproducible() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    profile = session.dut_profile

    def build(seed: int) -> list[list[str]]:
        return [
            create_dut(profile, seed=seed, x=float(x), y=0.0).defect_summary()
            for x in range(0, 120, 7)
        ]

    assert build(42) == build(42)
    assert build(42) != build(43)
    assert any(entry for entry in build(42)), "a 90% yield profile must still produce bad dies"


def test_yield_sampler_respects_pass_rate() -> None:
    session = load_session(CONFIGS / "demo_ft" / "session.yaml")
    spec = session.dut_profile.yield_
    sampler = YieldSampler(spec, seed=3, wafer_radius=None)
    dut = CounterModel({}, None)
    bad = sum(1 for _ in range(4000) if sampler.sample(dut, 0.0, 0.0))
    expected = 1.0 - spec.pass_rate
    assert abs(bad / 4000 - expected) < 0.05


def test_edge_dies_fail_more_often_than_the_centre() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    sampler = YieldSampler(session.dut_profile.yield_, seed=5, wafer_radius=75.0)
    centre = sampler.fail_probability(0.0, 0.0)
    edge = sampler.fail_probability(74.0, 0.0)
    assert edge > centre


def test_sample_yield_false_keeps_known_good_die() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    dut = create_dut(session.dut_profile, seed=1, x=0.0, y=0.0, sample_yield=False)
    assert dut.defect_summary() == []
