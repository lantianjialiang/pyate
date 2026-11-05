"""GUI: the plugin page lists, loads and disables plugins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

pytest.importorskip("PySide6", reason="GUI extras are not installed")

from PySide6.QtWidgets import QApplication

from pyate.api import plugin_report, set_plugin_enabled
from pyate.gui.plugin_panel import PluginPanel

EXAMPLE_PLUGINS = Path(__file__).resolve().parents[2] / "examples" / "plugins"
ADC = EXAMPLE_PLUGINS / "plugin_adc_dut"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture()
def panel(qapp: QApplication) -> PluginPanel:
    """A fresh plugin page; whatever it enabled is switched off afterwards."""
    widget = PluginPanel()
    yield widget
    for entry in plugin_report()["plugins"]:
        if entry["name"] in {"plugin_adc_dut", "plugin_json_sink", "failwatch"}:
            set_plugin_enabled(entry["name"], False)
    widget.deleteLater()


def _panel_rows(panel: PluginPanel) -> list[tuple[str, str]]:
    rows = []
    for row in range(panel.table.rowCount()):
        rows.append((panel.table.item(row, 0).text(), panel.table.item(row, 1).text()))
    return rows


def test_plugin_page_lists_the_bundled_examples(panel: PluginPanel) -> None:
    names = [panel.available.item(row, 0).text()
             for row in range(panel.available.rowCount())]
    assert {"failwatch", "plugin_adc_dut", "plugin_json_sink"} <= set(names)
    row = names.index("plugin_adc_dut")
    description = panel.available.item(row, 2).text()
    assert "ADC" in description or "adc" in description


def test_plugin_page_shows_the_search_paths(panel: PluginPanel) -> None:
    text = panel.paths_label.text()
    assert "examples" in text and "plugins" in text
    assert panel.summary.text()


def test_plugin_page_loads_a_plugin_and_lists_its_registrations(qapp: QApplication,
                                                               panel: PluginPanel) -> None:
    assert panel.load_path(str(ADC))
    qapp.processEvents()
    rows = dict(_panel_rows(panel))
    assert rows.get("plugin_adc_dut") == "enabled"
    contributes = panel.table.item(
        [name for name, _ in _panel_rows(panel)].index("plugin_adc_dut"), 3).text()
    assert "dut:adc" in contributes and "testitem:adc_inl_dnl" in contributes
    assert "adc" in panel.detail.text()


def test_plugin_page_disables_and_enables_the_selected_plugin(qapp: QApplication,
                                                             panel: PluginPanel) -> None:
    assert panel.load_path(str(ADC))
    qapp.processEvents()
    panel.table.selectRow(0)
    panel.toggle_selected()
    qapp.processEvents()
    assert panel.table.item(0, 1).text() == "disabled"
    assert panel.table.item(0, 3).text() == "-"
    assert panel.toggle_btn.text() == "Enable"

    panel.toggle_selected()
    qapp.processEvents()
    assert panel.table.item(0, 1).text() == "enabled"
    assert "dut:adc" in panel.table.item(0, 3).text()


def test_plugin_page_marks_loaded_candidates(qapp: QApplication,
                                            panel: PluginPanel) -> None:
    assert panel.load_path(str(ADC))
    qapp.processEvents()
    states = {}
    for row in range(panel.available.rowCount()):
        states[panel.available.item(row, 0).text()] = panel.available.item(row, 1).text()
    assert states["plugin_adc_dut"] == "loaded"
    assert states["failwatch"] == "not loaded"
