"""E2E-GUI: drive the real Qt window offscreen and check what it renders."""

from __future__ import annotations

import os

import pytest

# The Qt platform plugin must be chosen before PySide6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

pytest.importorskip("PySide6", reason="GUI extras are not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pyate.gui.app import create_window  # noqa: E402
from pyate.gui.main_window import MainWindow  # noqa: E402
from pyate.gui.theme import canvas_colors, set_theme, stylesheet  # noqa: E402
from pyate.gui.widgets import WaferMapWidget  # noqa: E402

from conftest import CONFIGS, CP_SESSION  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """One QApplication shared by every GUI test."""
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture()
def window(qapp: QApplication) -> MainWindow:
    """An offscreen main window with the demo CP session loaded."""
    win = MainWindow(str(CP_SESSION))
    yield win
    win.close()


@pytest.fixture(scope="module")
def finished_window(qapp: QApplication, tmp_path_factory: pytest.TempPathFactory):
    """Window that already ran one small CP wafer (expensive, shared)."""
    out_dir = tmp_path_factory.mktemp("gui_run")
    win = MainWindow(str(CP_SESSION))
    # keep the run short and the repository clean
    win.run_panel.output_dir = str(out_dir)
    win.run_panel.extra_overrides = {"lot.wafers": ["W01"]}
    handle = win.run_smoke(sites=2)
    assert handle is not None
    assert handle.wait(180.0), "GUI run did not finish"
    win.gui_out_dir = out_dir
    yield win, handle
    win.close()


# --------------------------------------------------------------------- shell
def test_window_builds_every_tab(window: MainWindow) -> None:
    titles = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert titles == ["Config", "TestFlow", "Limits/Bins", "Run", "Batch", "Pattern"]


def test_window_loads_the_session(window: MainWindow) -> None:
    assert window.session_path == str(CP_SESSION)
    status = window.status.text()
    assert "CP1" in status and "site" in status and "dut" in status
    assert window.run_panel.stage_box.currentText() == "CP1"
    assert window.run_panel.session_path == str(CP_SESSION)
    assert window.run_panel.stage_box.count() >= 2  # CP1 and CP2
    assert window.run_panel.sites_spin.value() == window.config_vm.session.loaded.run.sites


def test_run_panel_picks_up_wafer_geometry(window: MainWindow) -> None:
    # the panel must mirror wafer.yaml instead of the widget defaults
    wafer = window.config_vm.session.loaded.wafer
    assert window.run_panel.wafer_size == pytest.approx(wafer.diameter_mm)
    assert window.run_panel.die_size == pytest.approx(max(wafer.die_size_mm))
    assert window.run_panel.wafer_size == pytest.approx(300.0)


def test_theme_switching_repaints_widgets(window: MainWindow) -> None:
    window.set_theme("light")
    light_css = window.styleSheet()
    light_canvas = canvas_colors()["bg"]
    window.set_theme("dark")
    assert light_css != window.styleSheet()
    assert light_canvas != canvas_colors()["bg"]
    assert stylesheet("light") != stylesheet("dark")
    set_theme("dark")


def test_config_panel_shows_the_configuration_tree(window: MainWindow) -> None:
    tree = window.config_panel.tree
    assert tree.topLevelItemCount() > 0
    labels = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert any("session" in label.lower() or "tester" in label.lower() for label in labels)


def test_config_tree_splits_key_and_value(window: MainWindow) -> None:
    """The value column must be filled (labels are "key: value" strings)."""
    tree = window.config_panel.tree
    session = tree.topLevelItem(0)
    assert session.text(0) == "session"
    assert session.text(1)  # the session name
    pairs = {session.child(i).text(0): session.child(i).text(1)
             for i in range(session.childCount())}
    assert pairs["stage"]
    assert pairs["path"].endswith("session.yaml")


def test_number_formatting_keeps_cells_legible() -> None:
    from pyate.gui.panels import _fmt_num

    assert _fmt_num(None) == "-"
    assert _fmt_num(1) == "1"
    assert _fmt_num(0.0) == "0"
    assert _fmt_num(0.2496411896983) == "0.2496"   # long float is rounded
    assert _fmt_num(60.0) == "60"
    assert _fmt_num(2.5e-07) == "2.500e-07"        # tiny values keep precision
    assert _fmt_num(float("nan")).lower() == "nan"
    assert _fmt_num(True) == "yes"


def test_flow_panel_lists_the_stage_items(window: MainWindow) -> None:
    flow = window.flow_panel
    assert flow.tree.topLevelItemCount() > 0
    assert "node(s)" in flow.info.text()
    assert flow.disabled_items() == []


def test_limits_panel_is_populated(window: MainWindow) -> None:
    limits = window.limits_panel
    assert limits.table.rowCount() > 0
    assert limits.table.columnCount() == len(limits.COLUMNS)
    assert "limit rows" in limits.status.text()


def test_limits_edit_flows_back_into_the_session(window: MainWindow) -> None:
    panel = window.limits_panel
    hi_item = panel.table.item(0, 3)
    assert hi_item is not None
    original = hi_item.text()
    hi_item.setText("")
    panel.apply_changes()
    assert "applied" in panel.status.text()
    assert panel.table.item(0, 3).text() == ""  # open limit stays empty
    hi_item.setText(original)


def test_create_window_returns_a_main_window(qapp: QApplication) -> None:
    win = create_window(str(CP_SESSION), theme="light")
    try:
        assert isinstance(win, MainWindow)
        assert win.theme == "light"
        assert win.windowTitle().startswith("pyate")
    finally:
        win.close()
        set_theme("dark")


def test_run_without_session_is_rejected(qapp: QApplication) -> None:
    win = MainWindow(None)
    try:
        assert win.run_panel.session_path == ""
        with pytest.raises(RuntimeError):
            win.run_smoke()
    finally:
        win.close()


# ----------------------------------------------------------------- run + map
def test_gui_run_produces_a_summary(finished_window) -> None:
    window, handle = finished_window
    summary = handle.summary
    assert summary is not None
    assert summary.total_tested > 0
    assert summary.yield_pct > 0.0
    snapshot = window.run_panel.vm.snapshot()
    assert snapshot["parts"] == summary.total_tested
    assert snapshot["finished"] is True
    assert snapshot["error"] == ""


def test_view_model_collects_wafer_dies(finished_window) -> None:
    window, handle = finished_window
    vm = window.run_panel.vm
    assert vm.unit_kind == "wafer"
    assert vm.unit == "W01"
    # the map only holds the wafer being probed, the bins cover the whole lot
    unit = handle.summary.units[-1]
    assert len(vm.dies) == unit.tested
    assert set(vm.bins) <= set(range(1, 10))
    assert sum(vm.bins.values()) == handle.summary.total_tested
    assert vm.bins == dict(handle.summary.bin_counts)


def test_tick_renders_labels_and_table(finished_window) -> None:
    window, _handle = finished_window
    panel = window.run_panel
    panel.vm._dirty = True  # bypass the render throttle for the test
    panel.vm._last_render = 0.0
    panel._tick()
    assert "parts:" in panel.parts_label.text()
    assert "%" in panel.yield_label.text()
    assert panel.datalog.rowCount() > 0
    assert panel.progress.value() == 100 or panel.progress.value() > 0


def test_site_filter_narrows_the_datalog(finished_window) -> None:
    window, _handle = finished_window
    panel = window.run_panel
    panel.vm._dirty = True
    panel.vm._last_render = 0.0
    panel.site_filter.setCurrentIndex(1)  # site 1
    panel._tick()
    assert panel.datalog.rowCount() > 0
    assert panel.datalog.item(0, 0).text() == "1"


def test_gui_run_artifacts_land_in_the_output_dir(finished_window) -> None:
    window, handle = finished_window
    out_dir = window.gui_out_dir
    assert (out_dir / "summary.json").exists()
    assert any(path.suffix == ".stdf" for path in out_dir.iterdir())
    assert handle.summary.files


def test_wafer_map_exports_a_png(finished_window, tmp_path) -> None:
    window, _handle = finished_window
    wafer = window.run_panel.wafer
    wafer.set_data(window.run_panel.vm.dies,
                   wafer_size_mm=window.run_panel.wafer_size,
                   die_size_mm=window.run_panel.die_size)
    target = tmp_path / "wafer_map.png"
    assert wafer.export_png(str(target)) is True
    payload = target.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 1000


def test_empty_wafer_map_still_renders(qapp: QApplication, tmp_path) -> None:
    widget = WaferMapWidget()
    widget.resize(280, 280)
    target = tmp_path / "empty.png"
    assert widget.export_png(str(target)) is True
    assert target.read_bytes().startswith(b"\x89PNG")


# ----------------------------------------------------------------------- batch
def test_batch_panel_loads_jobs(window: MainWindow) -> None:
    batch = CONFIGS / "demo_batch" / "batch.yaml"
    assert batch.exists()
    panel = window.batch_panel
    panel.path_edit.setText(str(batch))
    panel._load()
    assert panel.table.rowCount() >= 2
    assert "job" in panel.status.text()


def test_pattern_panel_runs_a_pattern(window: MainWindow) -> None:
    panel = window.pattern_panel
    panel.path_edit.setText(str(CONFIGS / "demo_cp" / "patterns" / "cnt_basic.pat"))
    panel.cycles.setValue(5000)
    panel.run_pattern()
    text = panel.output.toPlainText()
    assert "pattern : cnt_basic" in text
    assert "result  : PASS" in text


def test_pattern_panel_reports_a_missing_file(window: MainWindow, monkeypatch) -> None:
    panel = window.pattern_panel
    panel.path_edit.setText("")
    shown: list[str] = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information",
                        lambda *a, **k: shown.append("info"))
    panel.run_pattern()
    assert shown == ["info"]


def test_pattern_panel_loads_models(window: MainWindow) -> None:
    panel = window.pattern_panel
    assert panel.dut_box.count() > 0
    assert "composite" in [panel.dut_box.itemText(i) for i in range(panel.dut_box.count())]
    assert panel.cycles.value() > 0


def test_run_page_has_a_summary_grouped_by_site(window: MainWindow) -> None:
    panel = window.run_panel
    labels = [panel.view_tabs.tabText(index) for index in range(panel.view_tabs.count())]
    assert "Summary" in labels
    assert panel.group_by.count() == 2
    assert panel.summary_table.columnCount() == 10
    assert panel.summary_probability is not None
    assert panel.probability is not None


def test_config_editor_opens_a_levels_document(window: MainWindow) -> None:
    window.config_panel._show_key("levels")
    editor = window.config_panel.editor
    assert editor.key == "levels"
    assert "sets" in editor.text.toPlainText()


def test_shmoo_plot_exports_csv(window: MainWindow, tmp_path) -> None:
    from pyate.api import run_shmoo

    payload = run_shmoo(
        window.config_vm.session,
        params={"vdd_steps": 2, "period_steps": 2, "cycles": 4},
    )
    window.run_panel.vm.set_shmoo(payload)
    window.run_panel.shmoo.set_data(payload)
    assert window.run_panel.shmoo.has_data
    target = tmp_path / "shmoo.csv"
    assert window.run_panel.shmoo.export_csv(str(target)) is True
    text = target.read_text(encoding="utf-8")
    assert "VDD" in text.splitlines()[0]
    assert len(text.splitlines()) == 3


def test_limit_edit_can_be_saved(qapp, tmp_path) -> None:
    import shutil

    from conftest import CONFIGS

    copy = tmp_path / "demo_cp"
    shutil.copytree(CONFIGS / "demo_cp", copy)
    win = MainWindow(str(copy / "session.yaml"))
    try:
        panel = win.limits_panel
        hi_item = panel.table.item(0, 3)
        assert hi_item is not None
        hi_item.setText("1.25")
        panel.apply_changes()
        assert "applied" in panel.status.text()
        written = win.config_panel.vm.save_limits()
        assert written.is_file()
        assert "1.25" in written.read_text(encoding="utf-8")
    finally:
        win.close()
        set_theme("dark")
