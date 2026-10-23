"""Component tests for the simulated HAL (DPS / PMU / relay / digital)."""

from __future__ import annotations

import pytest

from pyate.config.models import ChannelMapConfig, LevelsSet, TimingSet
from pyate.dut.base import Defect
from pyate.dut.models.counter import CounterModel
from pyate.hal.base import HALError, NotConnected, OverCurrent, ResourceConflict, SiteResources
from pyate.hal.simulated import SimulatedHAL, disconnect_all
from pyate.pattern.parser import parse_pattern_text

from conftest import CONFIGS
from pyate.config.loader import load_session


@pytest.fixture()
def hal() -> SimulatedHAL:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    backend = SimulatedHAL.from_session(session, site_count=2)
    backend.bind_dut(1, CounterModel({"bits": 8}, None))
    backend.bind_dut(2, CounterModel({"bits": 8}, None))
    return backend


def _sites(hal: SimulatedHAL) -> SiteResources:
    return hal.site(1)


# ------------------------------------------------------------------- topology
def test_site_count_and_accessors(hal: SimulatedHAL) -> None:
    assert hal.site_count == 2
    assert sorted(hal.sites) == [1, 2]
    assert hal.site(1).dps is not None
    assert hal.site(2).dps is not hal.site(1).dps


def test_unknown_site_raises(hal: SimulatedHAL) -> None:
    with pytest.raises(HALError):
        hal.site(0)
    with pytest.raises(HALError):
        hal.site(3)


def test_bind_dut_is_visible_through_site_resources(hal: SimulatedHAL) -> None:
    dut = CounterModel({}, None)
    hal.bind_dut(2, dut)
    assert hal.dut(2) is dut
    assert hal.site(2).dut() is dut


def test_pin_map_uses_channel_arithmetic(hal: SimulatedHAL) -> None:
    pin_map = hal.pin_map(1)
    assert pin_map["VDD"] == 1
    assert pin_map["CLK"] == 3
    assert pin_map["Q0"] == 8
    assert len(set(pin_map.values())) == len(pin_map)
    # site 2 is 48 channels higher (48-channel per-site block)
    assert hal.pin_map(2)["CLK"] == 51


def test_duplicate_channel_mapping_is_rejected() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    channel_map = ChannelMapConfig.model_validate({
        "pins": [
            {"pin": "CLK", "type": "in", "sites": {1: 1}},
            {"pin": "EN", "type": "in", "sites": {1: 1}},
        ],
    })
    with pytest.raises(ResourceConflict):
        SimulatedHAL(
            site_count=1,
            channel_map=channel_map,
            levels=session.levels,
            timing=session.timing,
        )


def test_from_session_uses_the_configured_site_count() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    backend = SimulatedHAL.from_session(session)
    assert backend.site_count == session.site_count()
    assert backend.site(1) is not None
    narrower = SimulatedHAL.from_session(session, site_count=1)
    assert narrower.site_count == 1


# ------------------------------------------------------------------------ DPS
def test_dps_measures_supply_current(hal: SimulatedHAL) -> None:
    dps = _sites(hal).dps
    dps.connect("VDD")
    dps.set_voltage("VDD", 3.3)
    current = dps.measure_current("VDD")
    assert 1e-6 < current < 0.05
    assert dps.is_connected("VDD")


def test_dps_raises_when_not_connected(hal: SimulatedHAL) -> None:
    with pytest.raises(NotConnected):
        _sites(hal).dps.measure_current("VDD")


def test_dps_enforces_the_current_limit(hal: SimulatedHAL) -> None:
    dps = _sites(hal).dps
    dut = hal.dut(1)
    assert dut is not None
    dut.inject(Defect("high_idd", factor=40.0))
    dps.set_voltage("VDD", 3.3)
    dps.set_current_limit("VDD", 1e-6)
    dps.connect("VDD")
    with pytest.raises(OverCurrent):
        dps.measure_current("VDD")


def test_dps_rejects_a_zero_current_limit(hal: SimulatedHAL) -> None:
    with pytest.raises(HALError):
        _sites(hal).dps.set_current_limit("VDD", 0.0)


def test_dps_disconnect_removes_power(hal: SimulatedHAL) -> None:
    dps = _sites(hal).dps
    dut = hal.dut(1)
    dps.connect("VDD")
    dps.set_voltage("VDD", 3.3)
    assert dut is not None and dut.powered
    dps.disconnect("VDD")
    assert not dut.powered
    assert not dps.is_connected("VDD")


def test_dps_status_reports_programming(hal: SimulatedHAL) -> None:
    dps = _sites(hal).dps
    dps.connect("VDD")
    dps.set_voltage("VDD", 3.0)
    status = dps.status()["VDD"]
    assert status["voltage"] == pytest.approx(3.0)
    assert status["connected"] is True


def test_dps_voltage_reading_tracks_the_programmed_value(hal: SimulatedHAL) -> None:
    dps = _sites(hal).dps
    dps.connect("VDD")
    dps.set_voltage("VDD", 3.3)
    assert dps.measure_voltage("VDD") == pytest.approx(3.3, abs=0.05)


# ------------------------------------------------------------------------ PMU
def test_pmu_continuity_measures_the_protection_diode(hal: SimulatedHAL) -> None:
    pmu = _sites(hal).pmu
    pmu.force_current("RSTN", -1e-4)
    volts = pmu.measure_voltage("RSTN")
    assert -1.0 < volts < -0.3


def test_pmu_leakage_measurement(hal: SimulatedHAL) -> None:
    pmu = _sites(hal).pmu
    pmu.force_voltage("RSTN", 1.0)
    current = pmu.measure_current("RSTN")
    assert current > 0
    dut = hal.dut(1)
    dut.inject(Defect("leakage", pin="RSTN", na=2000.0))
    assert pmu.measure_current("RSTN") > current + 1e-6


def test_pmu_requires_connection(hal: SimulatedHAL) -> None:
    with pytest.raises(NotConnected):
        _sites(hal).pmu.measure_current("RSTN")


def test_pmu_rejects_excessive_force_current(hal: SimulatedHAL) -> None:
    with pytest.raises(OverCurrent):
        _sites(hal).pmu.force_current("RSTN", 1.0)


def test_pmu_rejects_a_bad_irange(hal: SimulatedHAL) -> None:
    with pytest.raises(HALError):
        _sites(hal).pmu.force_voltage("RSTN", 1.0, irange=0.0)


def test_pmu_disconnect_opens_the_relay(hal: SimulatedHAL) -> None:
    resources = _sites(hal)
    resources.pmu.force_voltage("RSTN", 1.0)
    assert resources.relay.is_closed("pmu:RSTN")
    resources.pmu.disconnect("RSTN")
    assert not resources.relay.is_closed("pmu:RSTN")
    assert not resources.pmu.is_connected("RSTN")


# ---------------------------------------------------------------------- relay
def test_relay_matrix_tracks_closed_contacts(hal: SimulatedHAL) -> None:
    relay = _sites(hal).relay
    relay.close("dps:VDD")
    relay.close("pmu:Q0")
    assert sorted(relay.closed_relays()) == ["dps:VDD", "pmu:Q0"]
    relay.open("dps:VDD")
    assert not relay.is_closed("dps:VDD")
    relay.open_all()
    assert relay.closed_relays() == []


# -------------------------------------------------------------------- digital
def test_digital_run_reports_a_pattern_result(hal: SimulatedHAL) -> None:
    resources = _sites(hal)
    pattern = parse_pattern_text("""
pattern quick;
pins CLK, RSTN, EN, Q0, Q1;
label start:
  CLK=C RSTN=0 EN=0 Q=LL;
  CLK=C RSTN=1 EN=1 Q=HL;
  halt
""")
    resources.digital.apply_levels(LevelsSet(name="lv"))
    resources.digital.apply_timing(TimingSet(name="t"))
    resources.digital.load_pattern(pattern)
    resources.digital.set_vdd(3.3)
    result = resources.digital.run()
    assert result.cycles >= 2
    assert isinstance(result.passed, bool)


def test_digital_requires_a_bound_pattern(hal: SimulatedHAL) -> None:
    with pytest.raises(HALError):
        _sites(hal).digital.run()


def test_digital_requires_a_bound_dut() -> None:
    session = load_session(CONFIGS / "demo_cp" / "session.yaml")
    backend = SimulatedHAL.from_session(session, site_count=1)
    with pytest.raises(HALError):
        backend.site(1).digital.set_vdd(3.3)


# ------------------------------------------------------------------- lifecycle
def test_self_check_reports_problems_list(hal: SimulatedHAL) -> None:
    assert isinstance(hal.self_check(), list)
    assert hal.describe()["backend"] == "SimulatedHAL"


def test_simulated_time_accumulates_per_site(hal: SimulatedHAL) -> None:
    hal.reset_clock()
    hal.add_time(1, 2.5)
    assert hal.elapsed_ms(1) == pytest.approx(2.5)
    assert hal.elapsed_ms(2) == 0.0
    hal.site(1).dps.connect("VDD")
    assert hal.elapsed_ms(1) > 2.5


def test_reset_opens_everything(hal: SimulatedHAL) -> None:
    resources = _sites(hal)
    resources.pmu.force_voltage("RSTN", 1.0)
    resources.dps.connect("VDD")
    hal.reset()
    assert resources.relay.closed_relays() == []
    assert not resources.pmu.is_connected("RSTN")
    assert not resources.dps.is_connected("VDD")


def test_disconnect_all_helper_opens_the_matrix(hal: SimulatedHAL) -> None:
    resources = _sites(hal)
    resources.pmu.force_voltage("RSTN", 1.0)
    assert resources.relay.closed_relays()
    disconnect_all([resources])
    assert resources.relay.closed_relays() == []


def test_noise_is_deterministic_per_site(hal: SimulatedHAL) -> None:
    first = [hal.apply_noise(1, 1.0, 0.01) for _ in range(3)]
    other = SimulatedHAL.from_session(
        load_session(CONFIGS / "demo_cp" / "session.yaml"), site_count=2)
    second = [other.apply_noise(1, 1.0, 0.01) for _ in range(3)]
    assert first == second
    assert hal.apply_noise(1, 1.0, 0.0) == 1.0


def test_noise_rng_is_per_site(hal: SimulatedHAL) -> None:
    assert hal.rng_for(1) is not hal.rng_for(2)


def test_channel_map_with_buses_expands_to_individual_pins() -> None:
    channel_map = ChannelMapConfig.model_validate({
        "pins": [
            {"pin": "CLK", "type": "in", "channel": 1},
            {"pin": "Q[0:7]", "type": "out", "channel": 10},
        ],
    })
    expanded = channel_map.expand()
    assert expanded["Q7"][0].pin == "Q"
    assert expanded["Q7"][1] == 7
    bus = next(entry for entry in channel_map.pins if entry.pin == "Q")
    assert bus.width == 8
    # without a per-site map, the base channel is shared and only the bit
    # index shifts the channel number
    assert bus.channel_of(2, 3) == 13
    explicit = ChannelMapConfig.model_validate({
        "pins": [{"pin": "Q[0:7]", "type": "out", "sites": {1: 10, 2: 58}}],
    })
    bus = explicit.pins[0]
    assert bus.channel_of(2, 3) == 61
