"""Plugin page: what is loaded, what is available, and how to turn it on/off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api import (
    load_plugin,
    plugin_report,
    reload_plugins,
    scan_plugins,
    set_plugin_enabled,
)
from .panels import _mono, configure_table

__all__ = ["PluginPanel"]

#: Columns of the "loaded plugins" table.
_LOADED_COLUMNS = ("plugin", "status", "api", "contributes", "source")
#: Columns of the "available on disk" table.
_AVAILABLE_COLUMNS = ("plugin", "state", "description", "path")

#: Colour of the ``state`` cell in the "available" table.
_STATE_COLORS = {"loaded": "#35a86a", "disabled": "#d05c4c", "not loaded": "#8a929b"}


class PluginPanel(QWidget):
    """Manage plugins: load, enable/disable and inspect their contributions."""

    pluginsChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self.reload_btn = QPushButton("Reload all")
        self.reload_btn.clicked.connect(self.reload_all)
        self.file_btn = QPushButton("Load file...")
        self.file_btn.clicked.connect(self.load_file_dialog)
        self.dir_btn = QPushButton("Load folder...")
        self.dir_btn.clicked.connect(self.load_dir_dialog)
        for button in (self.reload_btn, self.file_btn, self.dir_btn):
            bar.addWidget(button)
        self.summary = QLabel("no plugins loaded")
        self.summary.setObjectName("muted")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        layout.addLayout(bar)

        layout.addWidget(self._build_card())

        heading = QHBoxLayout()
        heading.addWidget(QLabel("installed plugins"))
        heading.addStretch(1)
        self.toggle_btn = QPushButton("Disable")
        self.toggle_btn.clicked.connect(self.toggle_selected)
        self.toggle_btn.setEnabled(False)
        heading.addWidget(self.toggle_btn)
        layout.addLayout(heading)

        self.table = QTableWidget(0, len(_LOADED_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_LOADED_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        configure_table(self.table)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 330)
        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 3)

        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        avail_head = QHBoxLayout()
        avail_head.addWidget(QLabel("available in the search paths"))
        avail_head.addStretch(1)
        self.load_btn = QPushButton("Load selected")
        self.load_btn.clicked.connect(self.load_selected)
        self.load_btn.setEnabled(False)
        avail_head.addWidget(self.load_btn)
        layout.addLayout(avail_head)

        self.available = QTableWidget(0, len(_AVAILABLE_COLUMNS))
        self.available.setHorizontalHeaderLabels(list(_AVAILABLE_COLUMNS))
        self.available.verticalHeader().setVisible(False)
        self.available.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.available.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.available.setSelectionMode(QAbstractItemView.SingleSelection)
        self.available.setAlternatingRowColors(True)
        configure_table(self.available)
        self.available.horizontalHeader().setStretchLastSection(True)
        self.available.verticalHeader().setDefaultSectionSize(24)
        self.available.setColumnWidth(0, 180)
        self.available.setColumnWidth(1, 80)
        self.available.setColumnWidth(2, 340)
        self.available.itemSelectionChanged.connect(self._on_available_selection)
        self.available.itemDoubleClicked.connect(lambda _item: self.load_selected())
        layout.addWidget(self.available, 2)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(96)
        _mono(self.log)
        layout.addWidget(self.log)

        self.refresh()

    # -------------------------------------------------------------------- ui
    def _build_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)
        self.paths_label = QLabel("search paths: -")
        self.paths_label.setObjectName("filePath")
        self.paths_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.paths_label.setWordWrap(True)
        box.addWidget(self.paths_label)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.chips: list[QLabel] = []
        row.addStretch(1)
        box.addLayout(row)
        self.chip_row = row
        return card

    def _set_chips(self, texts: list[str]) -> None:
        for chip in self.chips:
            self.chip_row.removeWidget(chip)
            chip.deleteLater()
        self.chips = []
        for text in texts:
            chip = QLabel(text)
            chip.setObjectName("chip")
            self.chip_row.insertWidget(self.chip_row.count() - 1, chip)
            self.chips.append(chip)

    # --------------------------------------------------------------- refresh
    def refresh(self, report: dict[str, Any] | None = None) -> None:
        """Re-read the manager and rebuild both tables."""
        self.report = report if report is not None else plugin_report()
        data = self.report
        plugins = list(data.get("plugins", []))
        enabled = [entry for entry in plugins if entry.get("enabled")]
        self.paths_label.setText("search paths: " + "   ".join(data.get("paths", []) or ["-"]))
        self._set_chips([
            f"api {data.get('api_version', '-')}",
            f"policy {data.get('policy', '-')}",
            f"{len(enabled)} loaded",
            f"{data.get('total_contributions', 0)} contributions",
        ])
        self.summary.setText(
            f"{len(enabled)} of {len(plugins)} plugin(s) enabled"
            if plugins else "no plugins loaded"
        )
        self._fill_loaded(plugins)
        self._fill_available(scan_plugins())
        self._fill_log()
        self._on_selection()

    def _fill_loaded(self, plugins: list[dict[str, Any]]) -> None:
        selected = self.selected_plugin()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(plugins))
        for row, entry in enumerate(plugins):
            name = str(entry.get("name", ""))
            status = "enabled" if entry.get("enabled") else "disabled"
            values = (
                name,
                status,
                str(entry.get("api_version") or "-"),
                ", ".join(entry.get("contributions") or []) or "-",
                str(entry.get("source") or "-"),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 1:
                    item.setForeground(QColor("#35a86a" if entry.get("enabled") else "#d05c4c"))
                self.table.setItem(row, column, item)
            self.table.item(row, 0).setData(Qt.UserRole, name)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        if selected:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == selected:
                    self.table.selectRow(row)
                    break
        elif self.table.rowCount():
            self.table.selectRow(0)

    def _fill_available(self, entries: list[dict[str, Any]]) -> None:
        """Rebuild the lower table, keeping the selected row on the same path."""
        selected = self.selected_available_path()
        self.available.setSortingEnabled(False)
        self.available.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            state = str(entry.get("state") or ("loaded" if entry.get("loaded") else "not loaded"))
            values = (
                str(entry.get("name", "")),
                state,
                str(entry.get("doc") or "-"),
                str(entry.get("path", "")),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 1:
                    item.setForeground(QColor(_STATE_COLORS.get(state, "#8a929b")))
                self.available.setItem(row, column, item)
            self.available.item(row, 0).setData(Qt.UserRole, entry.get("path", ""))
            self.available.item(row, 0).setData(Qt.UserRole + 1, state)
        self.available.setSortingEnabled(True)
        if selected:
            for row in range(self.available.rowCount()):
                item = self.available.item(row, 0)
                if item is not None and item.data(Qt.UserRole) == selected:
                    self.available.selectRow(row)
                    break
        self._on_available_selection()

    def _fill_log(self) -> None:
        lines: list[str] = []
        for label, key in (("info", "messages"), ("warning", "warnings"), ("error", "errors")):
            for message in self.report.get(key, []):
                lines.append(f"{label}: {message}")
        for entry in self.report.get("rejected", []):
            lines.append(f"rejected: {entry}")
        self.log.setPlainText("\n".join(lines))

    # -------------------------------------------------------------- selection
    def selected_plugin(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else ""

    def select_plugin(self, name: str) -> bool:
        """Highlight ``name`` in the installed table (no-op when absent)."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.text() == name:
                self.table.selectRow(row)
                self._on_selection()
                return True
        return False

    def _on_selection(self) -> None:
        name = self.selected_plugin()
        entry = next((p for p in self.report.get("plugins", []) if p.get("name") == name), None)
        if entry is None:
            self.detail.setText("")
            self.toggle_btn.setEnabled(False)
            self.toggle_btn.setText("Disable")
            return
        doc = str(entry.get("doc") or "").strip()
        notes = "; ".join(entry.get("contributions") or []) or "no registrations (hooks only)"
        self.detail.setText(f"{name}: {doc or 'no description'}\nregisters: {notes}")
        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("Disable" if entry.get("enabled") else "Enable")
        self._sync_available_to(name)

    def _sync_available_to(self, name: str) -> None:
        """Highlight the same plugin in the lower table (keeps the two in step)."""
        for row in range(self.available.rowCount()):
            item = self.available.item(row, 0)
            if item is not None and item.text() == name:
                self.available.selectRow(row)
                return

    def selected_available_entry(self) -> tuple[str, str, str]:
        """``(name, path, state)`` of the row selected in the lower table."""
        row = self.available.currentRow()
        item = self.available.item(row, 0) if row >= 0 else None
        if item is None:
            return "", "", ""
        return (
            item.text(),
            str(item.data(Qt.UserRole) or ""),
            str(item.data(Qt.UserRole + 1) or ""),
        )

    def selected_available(self) -> tuple[str, str]:
        """``(path, state)`` of the row selected in the lower table."""
        _, path, state = self.selected_available_entry()
        return path, state

    def selected_available_path(self) -> str:
        """Path of the row selected in the lower table (empty when none)."""
        return self.selected_available()[0]

    def _on_available_selection(self) -> None:
        path, state = self.selected_available()
        if not path:
            self.load_btn.setEnabled(False)
            self.load_btn.setToolTip("")
            return
        self.load_btn.setEnabled(state != "loaded")
        if state == "loaded":
            self.load_btn.setToolTip("already loaded (see the table above)")
        elif state == "disabled":
            self.load_btn.setToolTip("loaded but disabled - click to enable it again")
        else:
            self.load_btn.setToolTip("")

    # ----------------------------------------------------------------- actions
    def toggle_selected(self) -> None:
        """Enable or disable the highlighted plugin."""
        name = self.selected_plugin()
        if not name:
            return
        entry = next((p for p in self.report.get("plugins", []) if p.get("name") == name), None)
        if entry is None:
            return
        enable = not entry.get("enabled")
        report = set_plugin_enabled(name, enable)
        self.refresh(report)
        self.pluginsChanged.emit(
            f"plugin {name}: {'enabled' if enable else 'disabled'} "
            "(applies to the next run)"
        )

    def load_selected(self) -> None:
        """Load (or re-enable) the plugin selected in the lower table."""
        name, path, state = self.selected_available_entry()
        if not path:
            return
        if state == "loaded":
            self.pluginsChanged.emit(f"{name}: already loaded, see the table above")
            return
        if state == "disabled":
            report = set_plugin_enabled(name, True)
            self.refresh(report)
            self.select_plugin(name)
            self.pluginsChanged.emit(f"plugin {name}: enabled")
            return
        self.load_path(path)

    def load_path(self, path: str) -> bool:
        """Load a plugin file/package by path and refresh the page."""
        if not path:
            return False
        before = set(self.report.get("loaded") or [])
        report = load_plugin(path)
        self.refresh(report)
        fresh = [name for name in report.get("loaded", []) if name not in before]
        name = fresh[0] if fresh else (Path(path).stem if path.endswith(".py") else Path(path).name)
        if report.get("loaded_now"):
            self.select_plugin(name)
            if len(fresh) > 1:
                self.pluginsChanged.emit(
                    f"plugins loaded from {Path(path).name}: {len(fresh)} ({', '.join(fresh)})")
            else:
                self.pluginsChanged.emit(f"plugin loaded: {name}")
            return True
        self.pluginsChanged.emit(f"could not load plugin: {name}")
        return False

    def load_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load plugin", "", "Python plugin (*.py)")
        if path:
            self.load_path(path)

    def load_dir_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Load plugin folder", "")
        if path:
            self.load_path(path)

    def reload_all(self) -> None:
        """Drop the manager and load everything again."""
        report = reload_plugins()
        self.refresh(report)
        self.pluginsChanged.emit(
            f"plugins reloaded: {len([p for p in report['plugins'] if p['enabled']])} enabled"
        )
