"""Independent voltage and sample-window checks for the simplified digital model."""
import pytest

from pyate.config.models import DutProfile, EdgeSpec, LevelsSet, TimingSet
from pyate.dut.registry import create_dut
from pyate.pattern.engine import PatternEngine
from pyate.pattern.parser import ParseError, parse_pattern_text

SOURCE = '''pattern sample;
pins CLK, RSTN, EN, Q0;
capture Q0;
CLK=C RSTN=0 EN=0 Q0=L;
CLK=C RSTN=1 EN=1 Q0=H;
halt;
'''


def run(levels=None, strobe=15, source=SOURCE, **options):
    dut = create_dut(DutProfile(model='counter'), sample_yield=False)
    dut.power(3.3)
    engine = PatternEngine(dut, levels or LevelsSet(name='lv'),
                           TimingSet(name='t',period_ns=20,edges=EdgeSpec(compare=strobe)))
    return engine.run(parse_pattern_text(source), **options)


def test_threshold_changes_verdict_without_changing_logic():
    assert run().passed
    high = run(levels=LevelsSet(name='strict',voh=3.25))
    assert not high.passed and high.first_fail_cycle == 2
    low = run(levels=LevelsSet(name='strict',vol=.01))
    assert not low.passed and low.first_fail_cycle == 1


def test_early_strobe_reports_unsettled_capture():
    assert run().captures['Q0'] == [0,1]
    early = run(strobe=5)
    assert not early.passed and early.captures['Q0'] == [-1]


def test_capture_window_works_without_compare_and_api_can_override_dsl():
    source = SOURCE.replace('Q0=L','Q0=X').replace('Q0=H','Q0=X')
    assert run(source=source,capture_start=2,capture_stop=2).captures == {'Q0':[1]}
    assert run(source=source,capture_pins=[]).captures == {}
    with pytest.raises(ParseError):
        parse_pattern_text(SOURCE.replace('capture Q0','capture unknown'))
