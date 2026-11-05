"""E2E-GUI: drive the real Qt window offscreen and check what it renders."""

from __future__ import annotations

import os

import pytest

# The Qt platform plugin must be chosen before PySide6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

pytest.importorskip("PySide6", reason="GUI extras are not installed")

from PySide6.QtWidgets import QApplication

from conftest import CONFIGS, CP_SESSION
from pyate.gui.app import create_window
from pyate.gui.main_window import MainWindow
from pyate.gui.theme import canvas_colors, set_theme, stylesheet
from pyate.gui.widgets import WaferMapWidget


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
    win.run_panel.extra_overrides = {"lot.wafers": ["W01"], "wafer.diameter_mm": 80}
    handle = win.run_smoke(sites=2)
    assert handle is not None
    assert handle.wait(180.0), "GUI run did not finish"
    win.gui_out_dir = out_dir
    yield win, handle
    win.close()


# --------------------------------------------------------------------- shell
def test_window_builds_every_tab(window: MainWindow) -> None:
    titles = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert titles == ["Config", "TestFlow", "Limits/Bins", "Run", "Pattern", "STDF",
                      "Plugins"]
    assert "Batch" not in titles
    assert window.theme == "light"
    assert "lantianjialiang" in window.copyright.text()


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
    set_theme("light")


def test_config_panel_shows_the_configuration_tree(window: MainWindow) -> None:
    tree = window.config_panel.tree
    assert tree.topLevelItemCount() > 0
    labels = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert any("session" in label.lower() or "tester" in label.lower() for label in labels)


def test_tray_widget_paints_input_slots_and_output_bins(qapp: QApplication) -> None:
    from pyate.gui.widgets import TrayWidget

    widget = TrayWidget()
    widget.resize(420, 320)
    widget.set_data({
        "title": "T01", "cols": 5, "rows": 2, "tested": 1, "total": 10, "jams": 1,
        "slots": [
            {"x": 0, "y": 0, "state": "picked", "part_id": "P00"},
            {"x": 1, "y": 0, "state": "testing", "part_id": "P01"},
            {"x": 2, "y": 0, "state": "untested", "part_id": ""},
        ],
        "sites": [{"site": 1, "status": "pass"}, {"site": 2, "status": "masked"}],
        "output": [{"bin": 1, "count": 3}],
    })
    widget.show()
    qapp.processEvents()
    widget.repaint()
    assert widget._hits
    widget.close()


def test_config_tree_shows_pass_and_fail_patterns(qapp: QApplication) -> None:
    win = MainWindow(str(CONFIGS / "demo_intro" / "session.yaml"))
    try:
        tree = win.config_panel.tree
        patterns = next(
            tree.topLevelItem(index)
            for index in range(tree.topLevelItemCount())
            if tree.topLevelItem(index).text(0) == "patterns"
        )
        names = [patterns.child(index).text(0) for index in range(patterns.childCount())]
        assert names == ["cnt_basic.pat", "cnt_fail.pat"]
        fail = patterns.child(1)
        assert "SO" in fail.text(1)
        tree.setCurrentItem(fail)
        qapp.processEvents()
        editor = win.config_panel.editor
        assert editor.tabs.tabText(0) == "说明"
        assert "SO" in editor.text.toPlainText()
        assert editor.save_btn.isEnabled() is False
        note = editor.form_layout.itemAt(0).widget()
        assert note is not None and "S0" in note.text()
    finally:
        win.close()


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


def test_datalog_reuses_cells_and_clears_failure_color(window: MainWindow) -> None:
    from PySide6.QtCore import Qt

    panel = window.run_panel
    panel.vm.add_measurement(test_num=1, name="IDD", site=1, value=1, passed=False)
    panel._refresh_rows()
    cell = panel.datalog.item(0, 4)
    assert cell.data(Qt.ForegroundRole) is not None
    panel.vm.reset()
    panel.vm.add_measurement(test_num=1, name="IDD", site=1, value=2, passed=True)
    panel._refresh_rows()
    assert panel.datalog.item(0, 4) is cell
    assert cell.text() == "2"
    assert cell.data(Qt.ForegroundRole) is None
    assert panel.datalog.updatesEnabled()
    assert not panel.datalog.signalsBlocked()


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


# ---------------------------------------------------------------- flow edits
def _copy_cp(tmp_path):
    import shutil

    copy = tmp_path / "demo_cp"
    shutil.copytree(CONFIGS / "demo_cp", copy)
    return copy


def _flow_button(flow, text: str):
    from PySide6.QtWidgets import QPushButton

    return next(button for button in flow.findChildren(QPushButton) if button.text() == text)


def test_flow_move_keeps_the_node_selected(qapp, tmp_path) -> None:
    """Repeated Move up/down clicks must not need a new click on the tree."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = MainWindow(str(_copy_cp(tmp_path) / "session.yaml"))
    try:
        win.show()
        win.tabs.setCurrentWidget(win.flow_panel)
        QApplication.processEvents()
        flow = win.flow_panel
        flow.stage_box.setCurrentText("CP1")
        flow._select_path([1])
        label = flow.tree.currentItem().text(0)
        down = _flow_button(flow, "Move down")
        QTest.mouseClick(down, Qt.LeftButton)
        QApplication.processEvents()
        current = flow.tree.currentItem()
        assert current is not None
        assert current.text(0) == label
        assert list(current.data(0, Qt.UserRole)) == [2]
        # a second click acts on the node the first click moved
        QTest.mouseClick(down, Qt.LeftButton)
        QApplication.processEvents()
        current = flow.tree.currentItem()
        assert current is not None and current.text(0) == label
        assert list(current.data(0, Qt.UserRole)) == [3]
        assert len(flow.tree.selectedItems()) == 1
        assert flow.tree.hasFocus()
    finally:
        win.close()
        set_theme("dark")


def test_flow_move_at_the_edge_keeps_the_node_selected(qapp, tmp_path) -> None:
    """A refused move (already first) keeps the selection too."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = MainWindow(str(_copy_cp(tmp_path) / "session.yaml"))
    try:
        win.show()
        win.tabs.setCurrentWidget(win.flow_panel)
        QApplication.processEvents()
        flow = win.flow_panel
        flow.stage_box.setCurrentText("CP1")
        flow._select_path([0])
        label = flow.tree.currentItem().text(0)
        QTest.mouseClick(_flow_button(flow, "Move up"), Qt.LeftButton)
        QApplication.processEvents()
        current = flow.tree.currentItem()
        assert current is not None and current.text(0) == label
        assert list(current.data(0, Qt.UserRole)) == [0]
    finally:
        win.close()
        set_theme("dark")


def test_flow_run_one_item_fills_the_run_page(qapp, tmp_path) -> None:
    """Run selected item feeds the Run page datalog, without a part record."""
    win = MainWindow(str(_copy_cp(tmp_path) / "session.yaml"))
    try:
        flow = win.flow_panel
        flow.stage_box.setCurrentText("CP1")
        labels = [flow.tree.topLevelItem(i).text(0) for i in range(flow.tree.topLevelItemCount())]
        index = labels.index("idd_static")
        flow._select_path([index])
        assert flow.item_site.count() >= 2  # demo_cp has 4 sites
        flow.item_site.setCurrentIndex(1)   # site 1
        report = win._run_single_item_now({
            "name": "idd_static", "test_num": 2000, "stage": "CP1", "site": 1,
        })
        assert report.passed
        assert len(report.values) == 1
        rows = win.run_vm.rows
        assert [row.test_num for row in rows] == [2000]
        assert rows[0].site == 1
        assert rows[0].value is not None
        # a partial flow has no bin: the wafer map and the part counter stay empty
        assert win.run_vm.parts == 0
        assert win.run_vm.dies == {}
    finally:
        win.close()
        set_theme("dark")


def test_flow_run_one_item_uses_the_form_parameters(qapp, tmp_path) -> None:
    """Values typed in the parameter form win over the saved program."""
    from PySide6.QtWidgets import QLineEdit

    win = MainWindow(str(_copy_cp(tmp_path) / "session.yaml"))
    try:
        flow = win.flow_panel
        flow.stage_box.setCurrentText("CP1")
        labels = [flow.tree.topLevelItem(i).text(0) for i in range(flow.tree.topLevelItemCount())]
        flow._select_path([labels.index("fmax_search")])
        editor = flow._param_widgets.get("cycles")
        assert isinstance(editor, QLineEdit)
        editor.setText("7")
        sent: list[dict] = []
        flow.runItemRequested.connect(sent.append)
        flow.run_selected_item()
        assert sent and sent[0]["params"]["cycles"] == 7
        assert sent[0]["test_num"] == 4000
        assert sent[0]["site"] == 1
        assert flow.item_button.isEnabled() is False  # disabled while running
    finally:
        win.close()
        set_theme("dark")


def test_flow_run_one_item_refuses_a_group(qapp, tmp_path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItem

    win = MainWindow(str(_copy_cp(tmp_path) / "session.yaml"))
    try:
        flow = win.flow_panel
        flow.tree.clear()
        node = QTreeWidgetItem(flow.tree, ["group", "", "group"])
        node.setData(0, Qt.UserRole + 1, {"kind": "group", "label": "group"})
        flow.tree.setCurrentItem(node)
        flow.run_selected_item()
        assert "only a test item" in flow.item_status.text()
    finally:
        win.close()
        set_theme("dark")


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


def test_run_page_breakpoint_box_lists_the_flow(window: MainWindow) -> None:
    panel = window.run_panel
    choices = [panel.bp_box.itemText(i) for i in range(panel.bp_box.count())]
    assert any("idd_static" in text for text in choices)
    assert panel.state_label.text() == "state: idle"
    panel.bp_box.setCurrentIndex(0)
    panel._add_breakpoint()
    armed = int(panel.bp_box.currentData())
    assert panel.breakpoints == {armed}
    assert str(armed) in panel.bp_label.text()
    panel._clear_breakpoints()
    assert panel.breakpoints == set()
    assert panel.bp_label.text() == "breakpoints: none"


def test_run_panel_shows_the_breakpoint_hit(qapp: QApplication) -> None:
    from types import SimpleNamespace

    from pyate.gui.panels import RunPanel
    from pyate.gui.viewmodels.run_vm import RunViewModel

    panel = RunPanel(RunViewModel())
    panel.handle = SimpleNamespace(running=True, paused=True)
    panel._sync_run_state({"finished": False, "paused_at": {
        "test_num": 2000, "item": "idd_static", "site": 1,
    }})
    assert panel.state_label.text() == "state: paused"
    assert "hit 2000 idd_static" in panel.bp_label.text()
    assert panel.resume_btn.isEnabled() and panel.step_btn.isEnabled()
    assert not panel.start_btn.isEnabled() and not panel.pause_btn.isEnabled()
    panel.close()


def test_theme_menu_tracks_the_light_default(window: MainWindow) -> None:
    assert window.theme_actions["light"].isChecked()
    assert not window.theme_actions["dark"].isChecked()
    window.set_theme("dark")
    assert window.theme_actions["dark"].isChecked()
    assert not window.theme_actions["light"].isChecked()
    window.set_theme("light")
    assert window.theme_actions["light"].isChecked()


def test_menu_items_reserve_room_for_shortcuts() -> None:
    # regression: "Open session..." used to run into "Ctrl+O"
    for theme in ("light", "dark"):
        css = stylesheet(theme)
        assert "QMenu::item { padding:" in css


def test_stdf_panel_shows_compact_stats(qapp: QApplication, finished_window) -> None:
    from pyate.gui.stdf_panel import StdfPanel

    win, _handle = finished_window
    files = sorted(win.gui_out_dir.glob("*.stdf"))
    assert files, "the finished run wrote no STDF"
    panel = StdfPanel()
    panel.load_file(str(files[0]))
    assert panel.tiles["parts"].value.text() != "-"
    assert panel.tiles["yield"].value.text().endswith("%")
    assert panel.meta_label.text().startswith("lot ")
    assert panel._chips, "bins should render as chips"
    assert panel.records_label.text().startswith("records:")
    assert panel.table.rowCount() > 0
    panel.close()


def test_run_page_has_a_summary_grouped_by_site(window: MainWindow) -> None:
    panel = window.run_panel
    labels = [panel.view_tabs.tabText(index) for index in range(panel.view_tabs.count())]
    assert "Summary" in labels
    assert "Charts" not in labels
    assert [box.text() for box in panel.group_by.findChildren(type(panel.site_checks[0]))] == [
        "site", "wafer", "part",
    ]
    assert panel.group_by.groups() == ()
    assert panel.datalog.columnCount() == 9
    assert panel.summary_table.columnCount() == 16
    assert panel.summary_table.horizontalHeaderItem(15).text() == "cpk"
    window.show()
    panel.view_tabs.setCurrentIndex(labels.index("Summary"))
    QApplication.processEvents()
    panel.summary_histogram.set_limits(0.5, 1.5)
    panel.summary_histogram.set_bins([(0.0, 1.0, 3), (1.0, 2.0, 1)])
    panel.summary_trend.set_limits(0.5, 1.5)
    panel.summary_trend.set_values([0.4, 0.8, 1.2, 1.6])
    panel.summary_probability.set_limits(0.5, 1.5)
    panel.summary_probability.set_points([(0.4, -1.0), (1.0, 0.0), (1.6, 1.0)])
    QApplication.processEvents()
    panel.summary_histogram.repaint()
    panel.summary_trend.repaint()
    panel.summary_probability.repaint()
    assert panel.summary_histogram._lo_pos is not None
    assert panel.summary_trend._hi_pos is not None
    assert panel.summary_probability._hi_pos is not None
    assert panel.clear_btn.text() == "Clear data"


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


def test_shmoo_button_uses_the_window_of_the_flow(qapp) -> None:
    """Run shmoo seeds range *and* grid from the flow's shmoo item."""
    import time

    win = MainWindow(str(CONFIGS / "demo_shmoo" / "session.yaml"))
    try:
        panel = win.run_panel
        # the controls start on the program's own grid, so one click reproduces
        # what a full run would plot instead of a coarser 12x12 version
        assert (panel.shmoo_vdd.value(), panel.shmoo_period.value(),
                panel.shmoo_cycles.value()) == (21, 31, 32)
        assert "demo_shmoo" in panel.shmoo_info.text()
        panel.shmoo_vdd.setValue(4)
        panel.shmoo_period.setValue(4)
        seen: list[dict] = []
        panel.shmooReady.connect(seen.append)
        panel.run_shmoo_once()
        deadline = time.time() + 120
        while not seen and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        assert seen, "shmoo did not finish"
        payload = seen[0]
        assert payload["y"][0] == pytest.approx(2.6)
        assert payload["x"][0] == pytest.approx(8.0)
        assert payload["x"][-1] == pytest.approx(128.0)
        assert len(payload["x"]) == 4
        assert panel.shmoo.has_data
    finally:
        win.close()
        set_theme("dark")


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
