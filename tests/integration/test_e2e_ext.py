"""E2E-EXT: load the reference ADC plugin and test its DUT through the API."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyate.api import RunOptions, create_run
from pyate.datalog.stdf.reader_check import verify_stdf
from pyate.dut.registry import is_registered
from pyate.plugins import load_plugins, reset_manager
from pyate.testitems.registry import is_registered as item_registered

PLUGINS = Path(__file__).resolve().parents[2] / "examples" / "plugins"
ADC_PLUGIN = PLUGINS / "plugin_adc_dut"
ADC_SESSION = ADC_PLUGIN / "example" / "session.yaml"

pytestmark = pytest.mark.skipif(not ADC_SESSION.is_file(), reason="example plugin missing")


@pytest.fixture(scope="module")
def adc_run(tmp_path_factory):
    manager = load_plugins([ADC_PLUGIN], fresh=True)
    output = tmp_path_factory.mktemp("adc")
    overrides = {"run.sites": 2, "run.seed": 42, "lot.parts_per_tray": 20,
                 "handler.contact_fail_prob": 0.0}
    handle = create_run(ADC_SESSION, overrides, RunOptions(output_dir=output))
    handle.prepare()
    summary = handle.run()
    yield manager, summary, Path(output)
    reset_manager()


def test_plugin_registers_model_and_items(adc_run) -> None:
    manager, summary, output = adc_run
    assert is_registered("adc")
    assert item_registered("adc_inl_dnl")
    assert item_registered("adc_offset_gain")
    assert "adc" in manager.registry.summary()["dut_models"]


def test_adc_lot_runs_to_completion(adc_run) -> None:
    manager, summary, output = adc_run
    assert summary.stage == "EXT1"
    assert summary.total_parts == 20
    assert summary.total_tested == 20
    assert 0.0 < summary.yield_pct <= 100.0


def test_plugin_items_appear_in_the_stdf(adc_run) -> None:
    manager, summary, output = adc_run
    stdf = sorted(output.glob("*.stdf"))[0]
    from pyate.datalog.stdf.records import iter_records

    numbers = {values.get("TEST_NUM") for name, values in iter_records(
        stdf.read_bytes()) if name in ("PTR", "FTR")}
    assert 5000 in numbers, "adc_inl_dnl must be logged"
    assert 5100 in numbers, "adc_offset_gain must be logged"


def test_adc_stdf_is_structurally_valid(adc_run) -> None:
    manager, summary, output = adc_run
    report = verify_stdf(sorted(output.glob("*.stdf"))[0])
    assert report.ok, report.errors[:3]
    assert report.parts == summary.total_parts


def test_adc_measurements_are_physically_plausible(adc_run) -> None:
    manager, summary, output = adc_run
    from pyate.datalog.stdf.records import iter_records

    values = [
        values["RESULT"]
        for name, values in iter_records((sorted(output.glob("*.stdf"))[0]).read_bytes())
        if name == "PTR" and values.get("TEST_NUM") in (5000, 5100)
    ]
    assert values
    assert all(abs(value) < 1000 for value in values)
