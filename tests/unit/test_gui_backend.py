"""Shmoo payload, config round-trip, and run-control behaviour. No Qt."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from pyate.api import Session, normalize_shmoo, run_shmoo, shmoo_csv
from pyate.api import EventBus, EventKind
from pyate.config.loader import load_session
from pyate.config.models import FlowNode
from pyate.engine.controller import RunController
from pyate.engine.events import EventBus as EngineBus
from pyate.gui.viewmodels.run_vm import RunViewModel

from conftest import CONFIGS, CP_SESSION, FT_SESSION


def test_normalize_shmoo_accepts_the_test_item_payload() -> None:
    payload = normalize_shmoo({
        "vdd_axis": [2.7, 3.6],
        "period_axis": [10.0, 20.0],
        "matrix": [[1, 0], [1, 1]],
    })
    assert payload["x"] == [10.0, 20.0]
    assert payload["y"] == [2.7, 3.6]
    assert payload["pass"] == [[True, False], [True, True]]
    assert payload["pass_rate"] == 75.0
    text = shmoo_csv(payload)
    assert "period_ns" in text.splitlines()[0]
    assert "2.7,1,0" in text.replace(" ", "")


def test_summary_groups_by_site_and_probability_plot() -> None:
    bus = EventBus()
    model = RunViewModel().attach(bus)
    for site, value in ((1, 1.0), (1, 3.0), (2, 5.0)):
        bus.publish(EventKind.TEST_RESULT, {"result": {
            "test_num": 2000, "test_name": "idd_static", "site": site,
            "value": value, "unit": "A", "passed": value < 4.0,
            "lo_limit": 0.0, "hi_limit": 6.0,
        }})
    combined = model.summary_rows(by_site=False)
    assert len(combined) == 1
    assert combined[0].site is None
    assert combined[0].count == 3
    assert combined[0].passed == 2
    assert combined[0].failed == 1
    assert combined[0].mean == pytest.approx(3.0)
    assert combined[0].stdev == pytest.approx(2.0)
    assert combined[0].lo == pytest.approx(0.0)
    assert combined[0].hi == pytest.approx(6.0)
    assert combined[0].cp == pytest.approx(0.5)
    assert combined[0].cpk == pytest.approx(0.5)
    per_site = model.summary_rows(by_site=True)
    assert [(row.site, row.count) for row in per_site] == [(1, 2), (2, 1)]
    assert model.trend(2000, 1) == [1.0, 3.0]
    points = model.probability(2000)
    assert len(points) == 3
    assert points[0][0] < points[-1][0]
    assert points[0][1] < points[-1][1]
    assert model.histogram(2000, site=2)
    bus.publish(EventKind.UNIT_START, {"unit": "W01", "kind": "wafer"})
    bus.publish(EventKind.TEST_RESULT, {"result": {
        "test_num": 2000, "test_name": "idd_static", "site": 1,
        "value": 2.0, "passed": True,
    }})
    bus.publish(EventKind.UNIT_START, {"unit": "W02", "kind": "wafer"})
    bus.publish(EventKind.TEST_RESULT, {"result": {
        "test_num": 2000, "test_name": "idd_static", "site": 1,
        "value": 4.0, "passed": False,
    }})
    by_wafer = model.summary_rows(group="wafer")
    assert [(row.wafer, row.count) for row in by_wafer] == [("", 3), ("W01", 1), ("W02", 1)]
    both = model.summary_rows(groups=("site", "wafer"))
    assert {(row.site, row.wafer, row.count) for row in both} == {
        (1, "", 2), (2, "", 1), (1, "W01", 1), (1, "W02", 1),
    }
    assert all(row.part is None for row in both)


def test_inspect_stdf_reports_lot_stats_and_per_wafer_tests(tmp_path: Path) -> None:
    from pyate.api import inspect_stdf
    from pyate.datalog.stdf.writer import StdfWriter
    from pyate.engine.context import PartResult, TestResult

    path = tmp_path / "tiny.stdf"
    writer = StdfWriter(path, site_count=1)
    writer.write_mir(lot_id="LOT-T", job_name="INTRO", stage="CP1")
    writer.write_wir("W01")
    part = PartResult(part_id="P1", site=1, x=0, y=0, soft_bin=1, hard_bin=1, passed=True)
    writer.start_part(part)
    writer.write_result(TestResult(test_num=2000, test_name="idd_static", site=1, value=1.5, passed=True))
    writer.write_result(TestResult(test_num=2000, test_name="idd_static", site=1, value=2.5, passed=True))
    writer.end_part(part)
    writer.write_wrr(wafer_id="W01", good=1)
    writer.write_summaries()
    writer.close()

    report = inspect_stdf(path)
    assert report["ok"], report["errors"]
    assert report["stats"]["lot_id"] == "LOT-T"
    assert report["stats"]["parts"] == 1
    assert report["stats"]["good"] == 1
    assert report["stats"]["ptr_count"] == 2
    assert report["stats"]["wafers"][0]["wafer_id"] == "W01"
    assert any(row["bin"] == 1 for row in report["stats"]["soft_bins"])
    model = RunViewModel()
    for row in report["measurements"]:
        model.add_measurement(
            test_num=row["test_num"], name=row["name"], site=row["site"],
            value=row["value"], passed=row["passed"], wafer=row["wafer"],
        )
    grouped = model.summary_rows(group="wafer")
    assert grouped[0].wafer == "W01"
    assert grouped[0].count == 2
    assert grouped[0].mean == pytest.approx(2.0)


def test_intro_patterns_explain_the_failure_and_list_the_pass() -> None:
    session = Session(CONFIGS / "demo_intro" / "session.yaml")
    rows = {item["name"]: item for item in session.pattern_files()}
    assert set(rows) == {"cnt_basic.pat", "cnt_fail.pat"}
    assert "通过" in rows["cnt_basic.pat"]["summary"]
    fail = rows["cnt_fail.pat"]
    assert "SO" in fail["summary"]
    assert "H" in fail["note"]
    assert "S0" in fail["note"]


def test_intro_session_is_the_short_walkthrough() -> None:
    session = Session(CONFIGS / "demo_intro" / "session.yaml")
    report = session.validate()
    assert report.ok, report.format()
    info = session.info()
    assert info.sites == 1
    labels = [node["label"] for node in session.flow("CP1")]
    assert labels == ["idd_static", "functional", "delay_ms", "shmoo"]
    assert session.flow("CP1")[2]["test_num"] == 9000


def test_view_model_keeps_shmoo_from_a_test_result() -> None:
    bus = EventBus()
    model = RunViewModel().attach(bus)
    bus.publish(EventKind.TEST_RESULT, {"result": {
        "test_num": 9000, "test_name": "shmoo_pass_rate", "site": 1,
        "value": 50.0, "unit": "%", "passed": True,
        "extra": {"shmoo": {
            "vdd_axis": [1.0, 2.0], "period_axis": [10.0, 30.0],
            "matrix": [[0, 1], [1, 1]], "pass_rate": 75.0,
        }},
    }})
    assert model.shmoo()["pass"][0] == [False, True]
    assert model.histogram(9000)
    assert model.trend(9000) == [50.0]


def test_levels_round_trip_and_a_rejected_write_keeps_the_file(tmp_path: Path) -> None:
    target = tmp_path / "demo_cp"
    shutil.copytree(CONFIGS / "demo_cp", target)
    session = Session(target / "session.yaml")
    document = session.file_document("levels")
    before = Path(document.path).read_text(encoding="utf-8")
    saved = session.save_file_data("levels", document.data)
    assert saved.ok, saved.text
    assert session.file_document("levels").data == document.data
    written = Path(document.path).read_text(encoding="utf-8")
    rejected = session.save_file_data("levels", {"nope": 1})
    assert not rejected.ok
    assert Path(document.path).read_text(encoding="utf-8") == written
    assert "nope" not in written
    assert before


def test_flow_disable_is_saved_and_skipped(tmp_path: Path) -> None:
    target = tmp_path / "demo_cp"
    shutil.copytree(CONFIGS / "demo_cp", target)
    session = Session(target / "session.yaml")
    session.set_enabled("CP1", [0], False)
    saved = session.save_program()
    assert saved.ok, saved.text
    session.reload()
    assert session.flow("CP1")[0]["enabled"] is False

    from pyate.dut.registry import create_dut
    from pyate.engine.binning import BinningEngine
    from pyate.engine.context import SiteContext
    from pyate.engine.executor import FlowExecutor
    from pyate.hal.simulated import SimulatedHAL
    from pyate.pattern.parser import load_pattern
    from pyate.testitems.base import LimitsTable

    loaded = load_session(target / "session.yaml")
    hal = SimulatedHAL.from_session(loaded, site_count=1)
    dut = create_dut(loaded.dut_profile, seed=1, sample_yield=False)
    hal.bind_dut(1, dut)
    hal.site(1).dps.set_voltage("VDD", 3.3)
    hal.site(1).dps.connect("VDD")
    nodes = [
        FlowNode.model_validate({"item": "delay_ms", "test_num": 1,
                                 "params": {"ms": 0}, "enabled": False}),
        FlowNode.model_validate({"item": "delay_ms", "test_num": 2, "params": {"ms": 0}}),
    ]
    executor = FlowExecutor(
        program_nodes=nodes, stage="CP1", limits=LimitsTable(loaded.limits),
        binning=BinningEngine(loaded.binning), hal=hal,
        pattern_loader=lambda name: load_pattern(CONFIGS / "demo_cp" / "patterns" / name),
    )
    part = executor.run_part(SiteContext(site_id=1, dut=dut, pin_map=hal.pin_map(1)))
    assert [row.test_num for row in part.results] == [2]


def test_breakpoint_pauses_before_the_item() -> None:
    from pyate.dut.registry import create_dut
    from pyate.engine.binning import BinningEngine
    from pyate.engine.context import SiteContext
    from pyate.engine.executor import FlowExecutor
    from pyate.hal.simulated import SimulatedHAL
    from pyate.testitems.base import LimitsTable

    loaded = load_session(CP_SESSION)
    hal = SimulatedHAL.from_session(loaded, site_count=1)
    dut = create_dut(loaded.dut_profile, seed=3, sample_yield=False)
    hal.bind_dut(1, dut)
    pause = threading.Event()
    generation = {"n": 0}
    events = EngineBus()
    started: list[int] = []
    events.subscribe(EventKind.TEST_START, lambda event: started.append(int(event.data["test_num"])))
    nodes = [
        FlowNode.model_validate({"item": "delay_ms", "test_num": 1, "params": {"ms": 0}}),
        FlowNode.model_validate({"item": "delay_ms", "test_num": 2, "params": {"ms": 0}}),
    ]
    executor = FlowExecutor(
        program_nodes=nodes, stage="CP1", limits=LimitsTable(loaded.limits),
        binning=BinningEngine(loaded.binning), hal=hal, events=events,
        pause_event=pause, breakpoints={2},
        step_generation=lambda: generation["n"],
    )
    context = SiteContext(site_id=1, dut=dut, pin_map=hal.pin_map(1))
    holder: dict[str, object] = {}

    def run() -> None:
        holder["part"] = executor.run_part(context)

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(100):
        if pause.is_set():
            break
        time.sleep(0.02)
    assert pause.is_set()
    assert started == [1]
    generation["n"] = 1
    pause.clear()
    thread.join(2)
    assert not thread.is_alive()
    part = holder["part"]
    assert [row.test_num for row in part.results] == [1, 2]  # type: ignore[attr-defined]


def test_disabled_site_is_masked(tmp_path: Path) -> None:
    loaded = load_session(FT_SESSION)
    loaded.program.flows["FT1"] = [
        FlowNode.model_validate({"item": "delay_ms", "test_num": 1, "params": {"ms": 0}}),
    ]
    loaded.run.disabled_sites = [2]
    events = EngineBus()
    touch: list[dict] = []
    events.subscribe(EventKind.TOUCHDOWN, lambda event: touch.append(dict(event.data)))
    controller = RunController(
        loaded, site_count=2, part_ids=["A", "B"], sinks=[], resume=False,
        output_dir=tmp_path, events=events, stage="FT1",
    )
    summary = controller.run()
    assert summary.total_tested == 1
    assert touch and 2 in touch[0]["masked_sites"]
    assert touch[0]["slots"]


def test_run_shmoo_returns_a_plot(tmp_path: Path) -> None:
    target = tmp_path / "demo_cp"
    shutil.copytree(CONFIGS / "demo_cp", target)
    payload = run_shmoo(
        Session(target / "session.yaml"),
        params={"vdd_steps": 2, "period_steps": 2, "cycles": 4},
    )
    assert len(payload["x"]) == 2
    assert len(payload["y"]) == 2
    assert len(payload["pass"]) == 2
    assert payload["pass_rate"] >= 0.0
