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
from pyate.plugins import reset_manager

EXAMPLE_PLUGINS = Path(__file__).resolve().parents[2] / "examples" / "plugins"
ADC = EXAMPLE_PLUGINS / "plugin_adc_dut"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture()
def panel(qapp: QApplication) -> PluginPanel:
    """A fresh plugin page on a clean manager; whatever it enabled is switched off afterwards."""
    reset_manager()
    widget = PluginPanel()
    yield widget
    for entry in plugin_report()["plugins"]:
        if entry["name"] in {"plugin_adc_dut", "plugin_json_sink", "failwatch", "acme_pkg"}:
            set_plugin_enabled(entry["name"], False)
    widget.deleteLater()
    reset_manager()


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
    row = [name for name, _ in _panel_rows(panel)].index("plugin_adc_dut")
    panel.table.selectRow(row)
    panel.toggle_selected()
    qapp.processEvents()
    row = [name for name, _ in _panel_rows(panel)].index("plugin_adc_dut")
    assert panel.table.item(row, 1).text() == "disabled"
    assert panel.table.item(row, 3).text() == "-"
    assert panel.toggle_btn.text() == "Enable"

    panel.toggle_selected()
    qapp.processEvents()
    row = [name for name, _ in _panel_rows(panel)].index("plugin_adc_dut")
    assert panel.table.item(row, 1).text() == "enabled"
    assert "dut:adc" in panel.table.item(row, 3).text()


def test_plugin_page_marks_loaded_candidates(qapp: QApplication,
                                            panel: PluginPanel) -> None:
    assert panel.load_path(str(ADC))
    qapp.processEvents()
    states = {}
    for row in range(panel.available.rowCount()):
        states[panel.available.item(row, 0).text()] = panel.available.item(row, 1).text()
    assert states["plugin_adc_dut"] == "loaded"
    assert states["failwatch"] == "not loaded"


def _available_states(panel: PluginPanel) -> dict[str, str]:
    return {
        panel.available.item(row, 0).text(): panel.available.item(row, 1).text()
        for row in range(panel.available.rowCount())
    }


def test_load_selected_loads_the_package_not_its_submodules(
        panel: PluginPanel, qapp: QApplication, monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path) -> None:
    """The candidate path is the package directory; its modules must not become plugins."""
    project = tmp_path / "acme_pkg"
    package = project / "acme_pkg"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'acme_pkg'\n", encoding="utf-8")
    (package / "__init__.py").write_text(
        '"""Acme selection plugin."""\n'
        "from pyate.plugins import hookimpl\n"
        "from pyate.testitems.base import TestItem, TestItemParams\n\n\n"
        "class AcmeItem(TestItem):\n"
        "    name = 'acme_selected_item'\n"
        "    Params = TestItemParams\n\n"
        "    def execute(self, ctx, hal):\n"
        "        return [self.make_result(ctx, value=1.0, unit='V')]\n\n\n"
        "class Plugin:\n"
        "    @hookimpl\n"
        "    def pyate_api_version(self):\n"
        "        return '1.0'\n\n"
        "    @hookimpl\n"
        "    def pyate_register(self, registry):\n"
        "        registry.add_testitem('acme_selected_item', AcmeItem)\n",
        encoding="utf-8")
    (package / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
    candidate = {
        "name": "acme_pkg", "path": str(package), "kind": "package",
        "doc": "Acme selection plugin.", "version": "1.0", "hooks": ["pyate_register"],
    }

    def fake_scan(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        loaded = "acme_pkg" in plugin_report()["loaded"]
        return [dict(candidate, state="loaded" if loaded else "not loaded", loaded=loaded)]

    monkeypatch.setattr("pyate.gui.plugin_panel.scan_plugins", fake_scan)
    panel.refresh()
    try:
        panel.available.selectRow(0)
        qapp.processEvents()
        assert panel.load_btn.isEnabled()

        panel.load_selected()
        qapp.processEvents()

        rows = dict(_panel_rows(panel))
        assert rows.get("acme_pkg") == "enabled"
        assert "helpers" not in rows
        contributes = panel.table.item(
            [name for name, _ in _panel_rows(panel)].index("acme_pkg"), 3).text()
        assert "testitem:acme_selected_item" in contributes
        # both tables now agree, and the button stops offering to load it again
        assert _available_states(panel)["acme_pkg"] == "loaded"
        assert panel.selected_plugin() == "acme_pkg"
        assert not panel.load_btn.isEnabled()
    finally:
        set_plugin_enabled("acme_pkg", False)


def test_available_table_follows_disable_and_enable(
        panel: PluginPanel, qapp: QApplication) -> None:
    assert panel.load_path(str(ADC))
    qapp.processEvents()
    panel.toggle_selected()  # disables the highlighted plugin
    qapp.processEvents()
    assert _available_states(panel)["plugin_adc_dut"] == "disabled"

    row = [panel.available.item(index, 0).text()
           for index in range(panel.available.rowCount())].index("plugin_adc_dut")
    panel.available.selectRow(row)
    qapp.processEvents()
    assert panel.load_btn.isEnabled()  # re-enable from here

    panel.load_selected()
    qapp.processEvents()
    assert _available_states(panel)["plugin_adc_dut"] == "loaded"
    assert dict(_panel_rows(panel))["plugin_adc_dut"] == "enabled"
