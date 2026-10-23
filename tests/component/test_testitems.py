"""Component tests for the built-in test items (one DUT, one HAL, real results)."""

from __future__ import annotations

import pytest

from pyate.config.loader import load_session
from pyate.dut.base import Defect
from pyate.dut.registry import create_dut
from pyate.engine.context import SiteContext
from pyate.hal.simulated import SimulatedHAL
from pyate.testitems.base import LimitsTable
from pyate.testitems.registry import create_testitem, is_registered, list_testitems

from conftest import CONFIGS

SESSION = CONFIGS / "demo_cp"


def _pattern_loader(name: str) -> object:
    from pyate.pattern.parser import load_pattern

    candidate = SESSION / "patterns" / name
    return load_pattern(candidate if candidate.exists() else name)


@pytest.fixture()
def rig():
    """HAL + DUT + context wired the way the executor wires them."""
    session = load_session(SESSION / "session.yaml")
    hal = SimulatedHAL.from_session(session, site_count=1)
    dut = create_dut(session.dut_profile, seed=7, sample_yield=False)
    hal.bind_dut(1, dut)
    # a real flow powers the part before the parametric items run
    hal.site(1).dps.set_voltage("VDD", 3.3)
    hal.site(1).dps.connect("VDD")
    ctx = SiteContext(site_id=1, dut=dut, pin_map=hal.pin_map(1))
    ctx.extra["pattern_loader"] = _pattern_loader
    return session, hal, dut, ctx


def _run(name: str, params: dict | None = None, test_num: int = 1000, rig=None):
    session, hal, dut, ctx = rig
    item = create_testitem(name, test_num, params or {})
    item.setup(ctx, hal)
    try:
        results = item.execute(ctx, hal)
    finally:
        item.cleanup(ctx, hal)
    for result in results:
        if result.kind == "PTR" and result.value is not None:
            result.judge(result.lo_limit, result.hi_limit)
    return results


OUTPUT_PINS = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "S0", "S1"]


# ------------------------------------------------------------------- registry
def test_registry_exposes_every_documented_item() -> None:
    expected = {
        "continuity", "idd_static", "idd_dynamic", "vol_voh", "power_up_down",
        "leakage_ih_il", "functional", "delay_ms", "user_code", "sram_march",
        "fmax_search", "vdd_min_search", "shmoo",
    }
    assert expected <= set(list_testitems())
    assert is_registered("continuity")
    assert not is_registered("no_such_item")


def test_unknown_item_raises() -> None:
    with pytest.raises(KeyError):
        create_testitem("no_such_item", 1, {})


def test_params_schema_rejects_unknown_keys() -> None:
    with pytest.raises(Exception):
        create_testitem("continuity", 1, {"not_a_parameter": 1})


# ---------------------------------------------------------------- continuity
def test_continuity_passes_on_a_healthy_die(rig) -> None:
    results = _run("continuity", {"pins": ["RSTN", "EN", "Q0"]}, rig=rig)
    assert len(results) == 3
    assert all(result.value is not None and result.value < 0 for result in results)
    for index, result in enumerate(results):
        assert result.test_num == 1000 + index
        assert result.passed
        assert result.pin in ("RSTN", "EN", "Q0")


def test_continuity_detects_an_open_pin(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("open", pin="Q0"))
    results = _run("continuity", {"pins": ["Q0"]}, rig=rig)
    assert not results[0].passed
    assert results[0].fail_code == 2


def test_continuity_defaults_to_every_signal_pin(rig) -> None:
    session, hal, dut, ctx = rig
    results = _run("continuity", {}, rig=rig)
    assert len(results) == len(dut.signal_pins)


# ----------------------------------------------------------------------- IDD
def test_idd_static_passes_and_reports_amperes(rig) -> None:
    results = _run("idd_static", {}, rig=rig)
    assert len(results) == 1
    result = results[0]
    assert result.unit == "A"
    assert result.value is not None and result.value > 0


def test_idd_static_fails_with_a_high_idd_defect(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("high_idd", factor=6.0))
    item = create_testitem("idd_static", 1001, {"hi": 100e-6})
    results = item.execute(ctx, hal)
    result = results[0]
    limits = LimitsTable(session.limits)
    row = limits.lookup(1001)
    if row is None:
        result.judge(result.lo_limit, result.hi_limit)
    assert result.value is not None and result.value > 100e-6


def test_idd_dynamic_uses_the_pattern(rig) -> None:
    results = _run("idd_dynamic", {"pattern": "cnt_basic.pat", "cycles": 32}, rig=rig)
    assert results
    assert results[0].value is not None


# ----------------------------------------------------------------- VOH / VOL
def test_vol_voh_measures_rail_levels(rig) -> None:
    results = _run("vol_voh", {"pins": OUTPUT_PINS}, rig=rig)
    assert len(results) == len(OUTPUT_PINS)
    for result in results:
        assert result.unit == "V"
        assert result.value is not None
        assert 0.0 <= result.value <= 3.6


def test_power_up_down_reports_a_slew(rig) -> None:
    results = _run("power_up_down", {}, rig=rig)
    assert len(results) == 1
    assert results[0].value is not None


# ------------------------------------------------------------------- leakage
def test_leakage_measures_both_polarities(rig) -> None:
    results = _run("leakage_ih_il", {"pins": ["RSTN", "EN"]}, rig=rig)
    assert len(results) >= 2
    assert all(result.value is not None for result in results)
    assert {result.extra.get("polarity") for result in results} <= {"il", "ih", None}


def test_leakage_fails_with_a_leaky_pin(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("leakage", pin="RSTN", na=5000.0))
    results = _run("leakage_ih_il", {"pins": ["RSTN"], "hi": 100e-9}, rig=rig)
    assert any(not result.passed for result in results)


# ---------------------------------------------------------------- functional
def test_functional_passes_on_a_healthy_die(rig) -> None:
    results = _run("functional", {"pattern": "cnt_basic.pat"}, rig=rig)
    assert len(results) == 1
    result = results[0]
    assert result.kind == "FTR"
    assert result.passed, result.extra
    assert result.cycle_cnt > 0


def test_functional_reports_failing_pins(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("stuck_at", pin="Q2", value=0))
    results = _run("functional", {"pattern": "cnt_basic.pat"}, rig=rig)
    result = results[0]
    assert not result.passed
    assert "Q2" in result.fail_pins
    assert result.num_fail > 0
    assert result.fail_code == 6


def test_functional_missing_pattern_is_a_failure_not_an_exception(rig) -> None:
    results = _run("functional", {"pattern": "no_such_file.pat"}, rig=rig)
    assert not results[0].passed
    assert "pattern load failed" in results[0].extra["error"]


# ------------------------------------------------------------------ sram
def test_sram_march_passes_on_a_healthy_die() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    hal = SimulatedHAL.from_session(session, site_count=1)
    profile = session.dut_profile.model_copy(deep=True)
    profile.model = "sram"
    profile.params = {"depth": 16, "width": 4}
    dut = create_dut(profile, seed=1, sample_yield=False)
    hal.bind_dut(1, dut)
    ctx = SiteContext(site_id=1, dut=dut)
    item = create_testitem("sram_march", 3000, {"algorithm": "march_c_minus"})
    results = item.execute(ctx, hal)
    assert results and results[0].passed


# -------------------------------------------------------------- misc items
def test_delay_advances_simulated_time(rig) -> None:
    session, hal, dut, ctx = rig
    hal.reset_clock()
    results = _run("delay_ms", {"ms": 2.5}, rig=rig)
    assert results[0].value == pytest.approx(2.5)
    assert hal.elapsed_ms(1) >= 2.5


def test_user_code_calls_a_real_function(rig, tmp_path, monkeypatch) -> None:
    module = tmp_path / "user_checks.py"
    module.write_text(
        "def gain(ctx, hal, scale=1.0):\n"
        "    return 3.0 * scale\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    results = _run("user_code", {"callable": "user_checks.gain", "args": {"scale": 2.0}},
                   rig=rig)
    assert results[0].value == pytest.approx(6.0)


def test_user_code_reports_an_unresolvable_callable(rig) -> None:
    session, hal, dut, ctx = rig
    item = create_testitem("user_code", 1, {"callable": "no.such.function"})
    with pytest.raises(Exception):
        item.execute(ctx, hal)


# ----------------------------------------------------------- search items
def test_vdd_min_search_finds_the_working_voltage(rig) -> None:
    results = _run("vdd_min_search", {
        "pattern": "cnt_basic.pat", "vdd_start": 3.6, "vdd_stop": 2.4, "resolution": 0.2,
        "cycles": 32,
    }, rig=rig)
    assert len(results) == 1
    assert results[0].unit == "V"
    assert results[0].value is not None


def test_fmax_search_returns_a_frequency(rig) -> None:
    results = _run("fmax_search", {
        "pattern": "cnt_loop.pat", "period_min_ns": 20.0, "period_max_ns": 80.0,
        "resolution_ns": 10.0, "cycles": 16,
    }, rig=rig)
    assert len(results) == 1
    assert results[0].unit == "MHz"
    assert results[0].value is not None


def test_shmoo_summarises_a_two_by_two_grid(rig) -> None:
    results = _run("shmoo", {
        "pattern": "cnt_loop.pat", "vdd_steps": 2, "period_steps": 2, "cycles": 8,
    }, rig=rig)
    assert len(results) == 1
    result = results[0]
    assert result.unit == "%"
    assert result.value is not None
    payload = result.extra["shmoo"]
    assert payload["vdd_axis"] == [2.7, 3.6]
    assert len(payload["cells"]) == 4
    assert len(payload["matrix"]) == 2
    assert {cell["vdd"] for cell in payload["cells"]} == {2.7, 3.6}


# ------------------------------------------------------------------ limits
def test_limits_table_prefers_the_specific_row() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    table = LimitsTable(session.limits)
    row = table.lookup(2000, stage="CP1", temperature=25.0)
    assert row is not None
    assert row.test_num == 2000
    assert row.unit == "A"
    assert row.hi == pytest.approx(260e-6)
    assert table.lookup(999999) is None
    assert table.all_for(2000)
