"""E2E-FLOW: CP1 -> CP2 -> FT1 lot flow with known-good-die carry-over."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyate.api import LotRunner, Session
from pyate.datalog.stdf.reader_check import verify_stdf

from conftest import CP_SESSION, FT_SESSION, small_cp_overrides

FT_OVERRIDES = {
    "run.sites": 4,
    "run.seed": 42,
    "lot.trays": 1,
    "lot.parts_per_tray": 500,
    "handler.jam_prob": 0.0,
    "handler.contact_fail_prob": 0.0,
}


@pytest.fixture(scope="module")
def lot(tmp_path_factory):
    output = tmp_path_factory.mktemp("lot")
    cp = Session(CP_SESSION, small_cp_overrides())
    ft = Session(FT_SESSION, FT_OVERRIDES)
    runner = LotRunner(cp, stages=["CP1", "CP2", "FT1"], sessions={"FT1": ft},
                       output_dir=output)
    return runner.run(), Path(output)


def test_all_three_stages_ran_in_order(lot) -> None:
    report, output = lot
    assert [stage.stage for stage in report.stages] == ["CP1", "CP2", "FT1"]
    assert report.lot_id
    assert all(stage.summary.total_parts > 0 for stage in report.stages)


def test_second_cp_stage_only_tests_known_good_dies(lot) -> None:
    report, output = lot
    cp1, cp2, _ft = report.stages
    assert cp2.die_filter is not None
    assert len(cp2.die_filter) == cp1.passed
    assert cp2.tested == cp1.passed
    assert cp2.kgd <= cp1.kgd


def test_ft_only_loads_known_good_parts(lot) -> None:
    report, output = lot
    cp1, cp2, ft = report.stages
    assert ft.part_ids is not None
    assert len(ft.part_ids) == cp2.passed
    assert ft.tested == cp2.passed
    assert set(ft.part_ids) == {f"{w}-X{x}Y{y}" for w, x, y in sorted(cp2.kgd)}


def test_cumulative_yield_matches_the_product_of_the_stages(lot) -> None:
    report, output = lot
    expected = 100.0
    for stage in report.stages:
        expected *= stage.yield_pct / 100.0
    assert report.cumulative_yield() == pytest.approx(expected, abs=1e-6)
    assert report.cumulative_yield() < min(stage.yield_pct for stage in report.stages)


def test_each_stage_writes_its_own_artefacts(lot) -> None:
    report, output = lot
    for stage in report.stages:
        stage_dir = output / stage.stage
        assert (stage_dir / "summary.json").is_file()
        assert (stage_dir / "run_snapshot.yaml").is_file()
        stdf_files = list(stage_dir.glob("*.stdf"))
        assert stdf_files, stage.stage
        assert verify_stdf(sorted(stdf_files)[0]).ok


def test_lot_report_files_are_complete(lot) -> None:
    report, output = lot
    data = json.loads((output / "lot_report.json").read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in data["stages"]] == ["CP1", "CP2", "FT1"]
    assert data["stages"][1]["filtered_in"] == len(report.stages[1].die_filter or ())
    assert data["stages"][2]["part_ids"] == len(report.stages[2].part_ids or ())
    text = (output / "lot_report.md").read_text(encoding="utf-8")
    assert "Cumulative yield" in text
    assert "CP1" in text and "FT1" in text


def test_stage_sessions_can_differ(lot) -> None:
    report, output = lot
    cp1, _cp2, ft = report.stages
    assert Path(cp1.session).parent.name == "demo_cp"
    assert Path(ft.session).parent.name == "demo_ft"


def test_default_stage_list_comes_from_lot_yaml(tmp_path) -> None:
    cp = Session(CP_SESSION, small_cp_overrides())
    runner = LotRunner(cp, output_dir=tmp_path)
    assert runner.planned_stages() == ["CP1", "CP2", "FT1"]


def test_missing_stage_session_gives_a_clear_error(tmp_path) -> None:
    cp = Session(CP_SESSION, small_cp_overrides())
    runner = LotRunner(cp, stages=["CP1", "FT1"], output_dir=tmp_path)
    with pytest.raises(ValueError) as excinfo:
        runner.run()
    assert "handler.yaml" in str(excinfo.value)
