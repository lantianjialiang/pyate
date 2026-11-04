"""Operating temperature changes response, never the sampled physical population."""
import math

import pytest

from pyate.config.models import DutProfile
from pyate.dut.base import DUTError
from pyate.dut.registry import create_dut


@pytest.mark.parametrize('model', ['counter', 'composite'])
def test_thermal_response_matches_configured_coefficients(model):
    dut = create_dut(DutProfile(model=model), seed=23, sample_yield=False)
    dut.power(3.3)
    identity = (repr(dut.defects), dut.idd_scale, dut.leak_scale, dut.vth_shift)
    dut.set_temperature(25)
    current = dut.supply_current()
    leakage = dut.pin_leakage('RSTN',3.3)
    period = dut.min_period_ns()
    vmin = dut.vdd_min_effective
    diode = dut.continuity_vdiode('RSTN')
    dut.set_temperature(125)
    assert dut.supply_current() == pytest.approx(current * math.exp(.2))
    assert dut.pin_leakage('RSTN',3.3) == pytest.approx(leakage * 32)
    assert dut.min_period_ns() == pytest.approx(period * math.exp(.1))
    assert dut.vdd_min_effective == pytest.approx(vmin + .05)
    assert dut.continuity_vdiode('RSTN') == pytest.approx(diode - .2)
    assert identity == (repr(dut.defects), dut.idd_scale, dut.leak_scale, dut.vth_shift)
    dut.set_temperature(25)
    assert dut.supply_current() == current


@pytest.mark.parametrize('value', [math.nan, math.inf, -274])
def test_invalid_temperature_is_rejected(value):
    with pytest.raises(DUTError):
        create_dut(DutProfile(model='counter')).set_temperature(value)


def test_no_frequency_is_available_below_threshold():
    dut = create_dut(DutProfile(model='counter'))
    dut.power(.1)
    assert dut.max_freq_mhz() == 0
