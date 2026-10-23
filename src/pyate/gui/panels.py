"""Dock/tab panels of the pyate GUI."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api import ConfigProblem, EventBus, RunHandle, RunOptions, Session, run_shmoo
from .editors import FileEditor
from .theme import bins_colors
from .viewmodels import BatchViewModel, ConfigViewModel, RunViewModel
from .widgets import (
    BinBarWidget,
    HistogramWidget,
    PatternCompareWidget,
    ProbabilityWidget,
    ShmooWidget,
    TrayWidget,
    TrendWidget,
    WaferMapWidget,
)

__all__ = [
    "ConfigPanel",
    "FlowPanel",
    "LimitsPanel",
    "RunPanel",
    "BatchPanel",
    "PatternPanel",
]


# --------------------------------------------------------------------- helpers
def _fmt_num(value: Any) -> str:
    """Format a measured number for a table cell.

    Floats are rounded so a column stays legible (``0.2496`` instead of
    ``0.2496411896983``); very small/large magnitudes fall back to scientific
    notation, and ``None`` becomes a dash so empty cells never look like 0.
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
            return str(value)
        if value == 0.0:
            return "0"
        if abs(value) < 1e-3 or abs(value) >= 1e6:
            return f"{value:.3e}"
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _fmt_stat(value: float | None) -> str:
    """Summary cell. Missing statistics stay blank rather than looking like zero."""
    if value is None:
        return ""
    return _fmt_num(value)


SUMMARY_COLUMNS = [
    "test", "name", "site", "wafer", "part", "n", "pass", "fail",
    "mean", "stdev", "min", "max", "lo", "hi", "cp", "cpk",
]


def summary_cells(row: Any) -> list[str]:
    """Display text for one test-summary row, including capability."""
    return [
        str(row.test_num),
        row.name,
        "all" if row.site is None else str(row.site),
        "all" if not row.wafer else row.wafer,
        "all" if row.part is None else (row.part or "(none)"),
        str(row.count),
        str(row.passed),
        str(row.failed),
        _fmt_stat(row.mean),
        _fmt_stat(row.stdev),
        _fmt_stat(row.minimum),
        _fmt_stat(row.maximum),
        _fmt_stat(row.lo),
        _fmt_stat(row.hi),
        _fmt_stat(row.cp),
        _fmt_stat(row.cpk),
    ]


class SortCell(QTableWidgetItem):
    """Table cell that sorts by the value stored in ``UserRole``."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: N802 - Qt naming
        left = self.data(Qt.UserRole)
        right = other.data(Qt.UserRole)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not isinstance(left, bool) and not isinstance(right, bool):
                return float(left) < float(right)
        return (self.text() or "") < (other.text() or "")


class GroupByBox(QWidget):
    """Checkboxes for summary dimensions. Several can be on at once."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._boxes: dict[str, QCheckBox] = {}
        for name in ("site", "wafer", "part"):
            box = QCheckBox(name)
            box.toggled.connect(self._emit)
            self._boxes[name] = box
            row.addWidget(box)

    def groups(self) -> tuple[str, ...]:
        """Selected dimensions, in display order."""
        return tuple(name for name, box in self._boxes.items() if box.isChecked())

    def _emit(self, _checked: bool = False) -> None:
        self.changed.emit()


def configure_table(table: QTableWidget) -> None:
    """Make every column user-resizable and sortable by its header."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(False)
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(False)
    header.sortIndicatorChanged.connect(lambda *_args: header.setSortIndicatorShown(True))
    table.setSortingEnabled(True)


def _put_cell(table: QTableWidget, row: int, column: int, text: str,
              sort_value: Any) -> QTableWidgetItem:
    item = table.item(row, column)
    if not isinstance(item, SortCell):
        item = SortCell(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, column, item)
    elif item.text() != text:
        item.setText(text)
    item.setData(Qt.UserRole, sort_value if sort_value is not None else text)
    return item


def _split_label(label: str) -> tuple[str, str]:
    """Split ``"key: value"`` into its two halves (no separator → no value)."""
    key, sep, value = label.partition(": ")
    if not sep:
        return label, ""
    return key, value


def _tree_item(parent: QTreeWidgetItem | QTreeWidget, text: str,
               value: str = "", file_key: str = "") -> QTreeWidgetItem:
    item = QTreeWidgetItem([text, value])
    if file_key:
        item.setData(0, Qt.UserRole, file_key)
    if isinstance(parent, QTreeWidgetItem):
        parent.addChild(item)
    else:
        parent.addTopLevelItem(item)
    return item


def _mono(widget: QWidget) -> None:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    widget.setFont(font)


# ---------------------------------------------------------------- config panel
class ConfigPanel(QWidget):
    """Open a session, browse its configuration and validate it."""

    sessionOpened = Signal(str)
    filesSaved = Signal(str)

    def __init__(self, vm: ConfigViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to session.yaml")
        browse = QPushButton("Open...")
        browse.clicked.connect(self._browse)
        load = QPushButton("Load")
        load.clicked.connect(self._load)
        self.validate_btn = QPushButton("Validate")
        self.validate_btn.clicked.connect(self._validate)
        self.save_limits_btn = QPushButton("Save limits")
        self.save_limits_btn.clicked.connect(self.save_limits)
        for widget in (self.path_edit, browse, load, self.validate_btn,
                       self.save_limits_btn):
            row.addWidget(widget)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["configuration", "value"])
        self.tree.setColumnWidth(0, 220)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.tree)
        right = QSplitter(Qt.Vertical)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        _mono(self.output)
        right.addWidget(self.output)
        self.editor = FileEditor()
        self.editor.save_btn.clicked.connect(self._save_file)
        right.addWidget(self.editor)
        right.setSizes([140, 460])
        splitter.addWidget(right)
        splitter.setSizes([360, 640])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

    # ------------------------------------------------------------------ slots
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open session", "",
                                              "Session (*.yaml *.yml)")
        if path:
            self.path_edit.setText(path)
            self._load()

    def _load(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            return
        result = self.vm.open(path)
        self.refresh_tree()
        self.output.setPlainText(result.text)
        if result.ok:
            self.sessionOpened.emit(path)

    def _validate(self) -> None:
        result = self.vm.validate()
        self.output.setPlainText(result.text)

    def refresh_tree(self) -> None:
        """Rebuild the configuration tree.

        View-model labels are ``"key: value"`` strings; they are split so the
        key and the value sit in their own columns (that is what makes long
        file paths readable instead of being elided mid-path).
        """
        self.tree.clear()
        for node in self.vm.tree():
            self._add_config_node(self.tree, node)

    def _add_config_node(self, parent: Any, node: dict[str, Any]) -> None:
        text, value = _split_label(node.get("label", ""))
        item = _tree_item(parent, text, value, str(node.get("file_key") or ""))
        pattern_path = str(node.get("pattern_path") or "")
        if pattern_path:
            item.setData(0, Qt.UserRole + 1, pattern_path)
            item.setData(0, Qt.UserRole + 2, str(node.get("note") or ""))
        for child in node.get("children", []) or []:
            self._add_config_node(item, child)
        item.setExpanded(True)

    def _on_tree_selection(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        pattern_path = items[0].data(0, Qt.UserRole + 1)
        if pattern_path:
            self._show_pattern(str(pattern_path), str(items[0].data(0, Qt.UserRole + 2) or ""))
            return
        key = items[0].data(0, Qt.UserRole)
        if not key:
            return
        try:
            document = self.vm.file_document(str(key))
        except Exception as exc:  # noqa: BLE001 - shown next to the editor
            self.editor.status.setText(str(exc))
            return
        if document is None:
            return
        self.editor.load_document(document.key, document.path, document.kind,
                                  document.text, document.data, document.schema)

    def _show_pattern(self, path: str, note: str) -> None:
        """Show a ``.pat`` file. It is not a YAML config, so Save stays off."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            self.editor.status.setText(str(exc))
            return
        self.editor.load_document("pattern", path, "text", text, {"note": note}, {})

    def _save_file(self) -> None:
        """Write the open document after validation."""
        if not self.editor.key or self.editor.kind == "text":
            return
        mode, payload = self.editor.payload()
        if mode == "text":
            result = self.vm.save_text(self.editor.key, str(payload))
        else:
            result = self.vm.save_data(self.editor.key, payload)
        self.output.setPlainText(result.text)
        self.editor.status.setText(result.text)
        if result.ok:
            key = self.editor.key
            self.refresh_tree()
            self._show_key(key)
            self.filesSaved.emit(self.path_edit.text().strip())

    def _show_key(self, key: str) -> None:
        if not key:
            return
        try:
            document = self.vm.file_document(key)
        except Exception as exc:  # noqa: BLE001
            self.editor.status.setText(str(exc))
            return
        if document is None:
            return
        self.editor.load_document(document.key, document.path, document.kind,
                                  document.text, document.data, document.schema)

    def save_limits(self) -> None:
        """Write edited limits back to the session's CSV."""
        try:
            path = self.vm.save_limits()
        except Exception as exc:  # noqa: BLE001 - reported in the UI
            QMessageBox.warning(self, "save limits", str(exc))
            return
        self.output.appendPlainText(f"limits written to {path}")


# ------------------------------------------------------------------- flow panel
class _FlowTree(QTreeWidget):
    """Tree that reorders siblings and refuses to reparent a node."""

    dropped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        source = self.currentItem()
        if source is None:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        source_parent = source.parent()
        if target is not None and target is not source_parent and target.parent() is not source_parent:
            event.ignore()
            return
        super().dropEvent(event)
        self.dropped.emit()


class FlowPanel(QWidget):
    """Tree view of the test flow: enable, reorder, edit params and on_fail."""

    def __init__(self, vm: ConfigViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        self._loading = False
        self._param_widgets: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(QLabel("stage:"))
        self.stage_box = QComboBox()
        self.stage_box.currentTextChanged.connect(lambda _t: self.refresh())
        row.addWidget(self.stage_box)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self._move(1))
        save = QPushButton("Save flow")
        save.clicked.connect(self._persist)
        for widget in (up, down, save):
            row.addWidget(widget)
        row.addStretch(1)
        self.info = QLabel("")
        row.addWidget(self.info)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = _FlowTree()
        self.tree.setHeaderLabels(["node", "test #", "kind"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 80)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_select)
        self.tree.dropped.connect(self._on_drop)
        splitter.addWidget(self.tree)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.param_box = QGroupBox("parameters")
        self.param_form = QFormLayout(self.param_box)
        side_layout.addWidget(self.param_box)
        fail_box = QGroupBox("on fail")
        fail_form = QFormLayout(fail_box)
        self.fail_action = QComboBox()
        self.fail_action.addItems(["continue", "stop", "retest"])
        self.fail_bin = QSpinBox()
        self.fail_bin.setRange(-1, 255)
        self.fail_bin.setSpecialValueText("none")
        self.fail_bin.setValue(-1)
        self.fail_retries = QSpinBox()
        self.fail_retries.setRange(0, 10)
        fail_form.addRow("action", self.fail_action)
        fail_form.addRow("bin", self.fail_bin)
        fail_form.addRow("retries", self.fail_retries)
        apply = QPushButton("Apply to node")
        apply.clicked.connect(self._apply_node)
        fail_form.addRow("", apply)
        side_layout.addWidget(fail_box)
        side_layout.addStretch(1)
        splitter.addWidget(side)
        splitter.setSizes([640, 320])
        layout.addWidget(splitter, 1)
        self.disabled: set[str] = set()

    def refresh(self) -> None:
        """Reload the flow of the selected stage."""
        stages = self.vm.stages()
        if stages and [self.stage_box.itemText(i) for i in range(self.stage_box.count())] != stages:
            current = self.stage_box.currentText()
            self.stage_box.blockSignals(True)
            self.stage_box.clear()
            self.stage_box.addItems(stages)
            if current in stages:
                self.stage_box.setCurrentText(current)
            self.stage_box.blockSignals(False)
        self._loading = True
        self.tree.clear()
        stage = self.stage_box.currentText() or None
        nodes = self.vm.flow_tree(stage)
        for index, node in enumerate(nodes):
            self._add_node(self.tree, node, [index])
        self._loading = False
        self.info.setText(f"{len(nodes)} top level node(s)")

    def _add_node(self, parent: Any, node: dict[str, Any], path: list[int]) -> None:
        label = node.get("label", "")
        test_num = "" if node.get("test_num") is None else str(node.get("test_num"))
        item = QTreeWidgetItem([label, test_num, node.get("kind", "")])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled
                      | Qt.ItemIsDropEnabled)
        item.setCheckState(0, Qt.Checked if node.get("enabled", True) else Qt.Unchecked)
        item.setData(0, Qt.UserRole, tuple(path))
        item.setData(0, Qt.UserRole + 1, node)
        if isinstance(parent, QTreeWidgetItem):
            parent.addChild(item)
        else:
            parent.addTopLevelItem(item)
        item.setExpanded(True)
        for index, child in enumerate(node.get("children", []) or []):
            self._add_node(item, child, path + [index])

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._loading:
            return
        path = item.data(0, Qt.UserRole)
        stage = self.stage_box.currentText()
        if not path or not stage:
            return
        self.vm.set_enabled(stage, list(path), item.checkState(0) == Qt.Checked)
        self._persist()

    def _move(self, delta: int) -> None:
        item = self.tree.currentItem()
        stage = self.stage_box.currentText()
        path = item.data(0, Qt.UserRole) if item is not None else None
        if not path or not stage:
            return
        if not self.vm.move_flow(stage, list(path), delta):
            return
        self._persist()
        self.refresh()

    def _on_drop(self) -> None:
        stage = self.stage_box.currentText()
        item = self.tree.currentItem()
        if item is None or not stage:
            QTimer.singleShot(0, self.refresh)
            return
        parent = item.parent()
        count = parent.childCount() if parent is not None else self.tree.topLevelItemCount()
        order: list[int] = []
        parent_path: list[int] | None = None
        for index in range(count):
            child = parent.child(index) if parent is not None else self.tree.topLevelItem(index)
            path = child.data(0, Qt.UserRole)
            if not path:
                QTimer.singleShot(0, self.refresh)
                return
            parts = list(path)
            if parent_path is None:
                parent_path = parts[:-1]
            if parts[:-1] != parent_path:
                QTimer.singleShot(0, self.refresh)
                return
            order.append(int(parts[-1]))
        captured = (stage, list(parent_path or []), list(order))
        QTimer.singleShot(0, lambda: self._finish_drop(*captured))

    def _finish_drop(self, stage: str, parent_path: list[int], order: list[int]) -> None:
        result = self.vm.reorder_flow(stage, parent_path, order)
        if not result.ok:
            self.info.setText(result.text)
            self.refresh()
            return
        self._persist()
        self.refresh()

    def _on_select(self) -> None:
        item = self.tree.currentItem()
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self._param_widgets = {}
        if item is None:
            return
        node = item.data(0, Qt.UserRole + 1) or {}
        if node.get("kind") != "item":
            return
        schema = self.vm.item_schema(str(node.get("label") or ""))
        params = dict(node.get("params") or {})
        for name, prop in (schema.get("properties") or {}).items():
            value = params.get(name, prop.get("default"))
            if prop.get("type") == "boolean":
                editor: QWidget = QCheckBox()
                editor.setChecked(bool(value))  # type: ignore[attr-defined]
            elif "enum" in prop:
                box = QComboBox()
                for option in prop["enum"]:
                    box.addItem(str(option), option)
                index = box.findData(value)
                if index >= 0:
                    box.setCurrentIndex(index)
                editor = box
            else:
                edit = QLineEdit("" if value is None else str(value))
                editor = edit
            self._param_widgets[name] = editor
            self.param_form.addRow(name, editor)
        on_fail = node.get("on_fail") or {}
        action = str(on_fail.get("action") or "continue")
        index = self.fail_action.findText(action)
        if index >= 0:
            self.fail_action.setCurrentIndex(index)
        bin_value = on_fail.get("bin")
        self.fail_bin.setValue(-1 if bin_value is None else int(bin_value))
        self.fail_retries.setValue(int(on_fail.get("max_retries") or 0))

    def _apply_node(self) -> None:
        item = self.tree.currentItem()
        stage = self.stage_box.currentText()
        path = item.data(0, Qt.UserRole) if item is not None else None
        if item is None or not path or not stage:
            return
        node = item.data(0, Qt.UserRole + 1) or {}
        if node.get("kind") == "item":
            schema = self.vm.item_schema(str(node.get("label") or ""))
            params: dict[str, Any] = {}
            for name, prop in (schema.get("properties") or {}).items():
                widget = self._param_widgets.get(name)
                params[name] = _read_param(widget, prop)
            result = self.vm.set_item_params(stage, list(path), params)
            if not result.ok:
                self.info.setText(result.text)
                return
        bin_value = None if self.fail_bin.value() < 0 else int(self.fail_bin.value())
        result = self.vm.set_on_fail(
            stage, list(path), action=self.fail_action.currentText(),
            bin=bin_value, max_retries=self.fail_retries.value(),
        )
        if not result.ok:
            self.info.setText(result.text)
            return
        self._persist()
        self.refresh()

    def _persist(self) -> None:
        result = self.vm.save_program()
        self.info.setText(result.text)

    def disabled_items(self) -> list[str]:
        """Labels of the unchecked nodes."""
        out: list[str] = []

        def walk(item: QTreeWidgetItem) -> None:
            if item.childCount() == 0 and item.checkState(0) != Qt.Checked:
                out.append(item.text(0))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(index))
        return out


def _read_param(widget: QWidget | None, prop: dict[str, Any]) -> Any:
    """Read one generated parameter widget back into a Python value."""
    if widget is None:
        return prop.get("default")
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QComboBox):
        return widget.currentData()
    text = widget.text().strip() if isinstance(widget, QLineEdit) else ""
    if text == "":
        return None
    kind = prop.get("type")
    if kind == "integer":
        return int(float(text))
    if kind == "number":
        return float(text)
    return text


# ----------------------------------------------------------------- limits panel
class LimitsPanel(QWidget):
    """Editable limits table (test_num / lo / hi)."""

    COLUMNS = ["test_num", "name", "lo", "hi", "unit", "stage"]

    def __init__(self, vm: ConfigViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.refresh)
        apply_btn = QPushButton("Apply changes")
        apply_btn.clicked.connect(self.apply_changes)
        import_btn = QPushButton("Import CSV")
        import_btn.clicked.connect(self.import_csv)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self.export_csv)
        save_bins = QPushButton("Save bins")
        save_bins.clicked.connect(self.save_bins)
        for widget in (reload_btn, apply_btn, import_btn, export_btn, save_bins):
            row.addWidget(widget)
        row.addStretch(1)
        self.status = QLabel("")
        row.addWidget(self.status)
        layout.addLayout(row)

        self.tabs = QTabWidget()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tabs.addTab(self.table, "Limits")

        bins = QWidget()
        bins_layout = QVBoxLayout(bins)
        bin_row = QHBoxLayout()
        bin_row.addWidget(QLabel("pass bin"))
        self.pass_bin = QSpinBox()
        self.pass_bin.setRange(0, 255)
        bin_row.addWidget(self.pass_bin)
        bin_row.addWidget(QLabel("default fail"))
        self.fail_bin = QSpinBox()
        self.fail_bin.setRange(0, 255)
        bin_row.addWidget(self.fail_bin)
        bin_row.addStretch(1)
        bins_layout.addLayout(bin_row)
        self.soft_table = QTableWidget(0, 3)
        self.soft_table.setHorizontalHeaderLabels(["sbin", "name", "pass/fail"])
        self.hard_table = QTableWidget(0, 2)
        self.hard_table.setHorizontalHeaderLabels(["hbin", "name"])
        for table in (self.soft_table, self.hard_table):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.soft_table)
        split.addWidget(self.hard_table)
        bins_layout.addWidget(split, 1)
        self.tabs.addTab(bins, "Bins")
        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        """Reload rows from the session."""
        rows = self.vm.limits_rows()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, key in enumerate(self.COLUMNS):
                value = row.get(key)
                # lo/hi stay blank when unbounded; other columns are integers
                text = "" if value is None else _fmt_num(value)
                item = QTableWidgetItem(text)
                if key in ("test_num",):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if key in ("test_num", "lo", "hi"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, col_index, item)
        self.status.setText(f"{len(rows)} limit rows")
        self._fill_bins()

    def _fill_bins(self) -> None:
        data = self.vm.bins()
        self.pass_bin.setValue(int(data.get("pass_bin") or 1))
        self.fail_bin.setValue(int(data.get("default_fail_bin") or 0))
        soft = data.get("soft_bins") or []
        self.soft_table.setRowCount(len(soft))
        for index, row in enumerate(soft):
            self.soft_table.setItem(index, 0, QTableWidgetItem(str(row.get("sbin", ""))))
            self.soft_table.setItem(index, 1, QTableWidgetItem(str(row.get("name", ""))))
            self.soft_table.setItem(index, 2, QTableWidgetItem(str(row.get("pass_fail", "F"))))
        hard = data.get("hard_bins") or []
        self.hard_table.setRowCount(len(hard))
        for index, row in enumerate(hard):
            self.hard_table.setItem(index, 0, QTableWidgetItem(str(row.get("hbin", ""))))
            self.hard_table.setItem(index, 1, QTableWidgetItem(str(row.get("name", ""))))

    def import_csv(self) -> None:
        """Replace limits from a CSV file and write them back if they validate."""
        path, _ = QFileDialog.getOpenFileName(self, "Import limits", "", "CSV (*.csv)")
        if not path:
            return
        result = self.vm.import_limits_csv(Path(path).read_text(encoding="utf-8-sig"))
        self.status.setText(result.text)
        if result.ok:
            self.refresh()

    def export_csv(self) -> None:
        """Write the current limits to a CSV chosen by the user."""
        path, _ = QFileDialog.getSaveFileName(self, "Export limits", "limits.csv", "CSV (*.csv)")
        if not path:
            return
        result = self.vm.export_limits_csv(path)
        self.status.setText(result.text)

    def save_bins(self) -> None:
        """Write the bin tables to binning.yaml."""
        data = dict(self.vm.bins())
        data["pass_bin"] = int(self.pass_bin.value())
        data["default_fail_bin"] = int(self.fail_bin.value())
        soft: list[dict[str, Any]] = []
        for index in range(self.soft_table.rowCount()):
            sbin = self.soft_table.item(index, 0)
            name = self.soft_table.item(index, 1)
            flag = self.soft_table.item(index, 2)
            if sbin is None or not sbin.text().strip():
                continue
            soft.append({
                "sbin": int(float(sbin.text())),
                "name": "" if name is None else name.text(),
                "pass_fail": "P" if flag is not None and flag.text().strip().upper().startswith("P") else "F",
            })
        hard: list[dict[str, Any]] = []
        for index in range(self.hard_table.rowCount()):
            hbin = self.hard_table.item(index, 0)
            name = self.hard_table.item(index, 1)
            if hbin is None or not hbin.text().strip():
                continue
            hard.append({
                "hbin": int(float(hbin.text())),
                "name": "" if name is None else name.text(),
            })
        data["soft_bins"] = soft
        data["hard_bins"] = hard
        result = self.vm.save_binning(data)
        self.status.setText(result.text)

    def apply_changes(self) -> None:
        """Push edited lo/hi values into the session and validate."""
        changed = 0
        for row_index in range(self.table.rowCount()):
            test_num_item = self.table.item(row_index, 0)
            if test_num_item is None:
                continue
            try:
                test_num = int(float(test_num_item.text()))
            except ValueError:
                continue
            lo = self._value(row_index, 2)
            hi = self._value(row_index, 3)
            result = self.vm.update_limit(test_num, lo=lo, hi=hi)
            changed += 1
            if not result.ok:
                self.status.setText(result.text)
                return
        self.status.setText(f"applied {changed} row(s)")

    def _value(self, row: int, col: int) -> float | None:
        item = self.table.item(row, col)
        if item is None or not item.text().strip():
            return None
        try:
            return float(item.text())
        except ValueError:
            return None


# -------------------------------------------------------------------- run panel
class RunPanel(QWidget):
    """Run control, live datalog, wafer map and statistics."""

    shmooReady = Signal(object)

    def __init__(self, vm: RunViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        self.handle: RunHandle | None = None
        self.session_path = ""
        self.output_dir: str | None = None
        self.extra_overrides: dict[str, Any] = {}
        self.breakpoints: set[int] = set()
        self._log_level = "ALL"
        # geometry of the current session, used to scale the wafer map
        self.wafer_size = 300.0
        self.die_size = 10.0
        self._build()
        self.shmooReady.connect(self._on_shmoo_ready)
        self.timer = QTimer(self)
        self.timer.setInterval(50)  # throttled UI refresh
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ------------------------------------------------------------------- ui
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        controls = QGroupBox("run control")
        form = QHBoxLayout(controls)
        form.addWidget(QLabel("stage:"))
        self.stage_box = QComboBox()
        form.addWidget(self.stage_box)
        form.addWidget(QLabel("sites:"))
        self.sites_spin = QSpinBox()
        self.sites_spin.setRange(0, 64)
        self.sites_spin.setSpecialValueText("auto")
        form.addWidget(self.sites_spin)
        form.addWidget(QLabel("seed:"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_000_000_000)
        form.addWidget(self.seed_spin)
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_run)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_run)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.clicked.connect(self.resume_run)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self.stop_run)
        self.step_btn = QPushButton("Step")
        self.step_btn.clicked.connect(self.step_run)
        self.clear_btn = QPushButton("Clear data")
        self.clear_btn.clicked.connect(self.clear_results)
        for widget in (self.start_btn, self.pause_btn, self.resume_btn,
                       self.step_btn, self.stop_btn, self.clear_btn):
            form.addWidget(widget)
        form.addWidget(QLabel("break"))
        self.bp_spin = QSpinBox()
        self.bp_spin.setRange(0, 1_000_000)
        form.addWidget(self.bp_spin)
        bp_add = QPushButton("Set")
        bp_add.clicked.connect(self._add_breakpoint)
        bp_clear = QPushButton("Clear")
        bp_clear.clicked.connect(self._clear_breakpoints)
        form.addWidget(bp_add)
        form.addWidget(bp_clear)
        self.bp_label = QLabel("breakpoints: none")
        form.addWidget(self.bp_label)
        form.addStretch(1)
        layout.addWidget(controls)

        self.site_checks: list[QCheckBox] = []
        site_row = QHBoxLayout()
        site_row.addWidget(QLabel("enable sites:"))
        self.site_row = site_row
        site_host = QWidget()
        site_host.setLayout(site_row)
        layout.addWidget(site_host)

        stats_row = QHBoxLayout()
        self.unit_label = QLabel("unit: -")
        self.unit_label.setObjectName("stat")
        self.yield_label = QLabel("yield: -")
        self.yield_label.setObjectName("stat")
        self.parts_label = QLabel("parts: 0")
        self.parts_label.setObjectName("stat")
        self.bins_label = QLabel("bins: 0")
        self.bins_label.setObjectName("stat")
        self.uph_label = QLabel("uph: -")
        self.uph_label.setObjectName("stat")
        for label in (self.unit_label, self.parts_label, self.yield_label,
                      self.bins_label, self.uph_label):
            stats_row.addWidget(label)
        stats_row.addStretch(1)
        self.site_filter = QComboBox()
        self.site_filter.addItem("all sites", None)
        self.site_filter.currentIndexChanged.connect(lambda _i: self._on_site_filter())
        stats_row.addWidget(self.site_filter)
        layout.addLayout(stats_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        splitter = QSplitter(Qt.Horizontal)
        datalog_page = QWidget()
        datalog_layout = QVBoxLayout(datalog_page)
        datalog_layout.setContentsMargins(0, 0, 0, 0)
        self.datalog_count = QLabel("0 rows")
        datalog_layout.addWidget(self.datalog_count)
        self.datalog = QTableWidget(0, 9)
        self.datalog.setHorizontalHeaderLabels(
            ["site", "part", "test #", "name", "value", "unit", "lo", "hi", "result"])
        configure_table(self.datalog)
        for column, width in ((0, 50), (1, 140), (2, 70), (3, 140),
                              (4, 90), (5, 50), (6, 70), (7, 70), (8, 70)):
            self.datalog.setColumnWidth(column, width)
        self.datalog.verticalHeader().setVisible(False)
        self.datalog.setAlternatingRowColors(True)
        self.datalog.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.datalog.setSelectionBehavior(QAbstractItemView.SelectRows)
        _mono(self.datalog)
        self.datalog.itemSelectionChanged.connect(self._on_datalog_select)
        datalog_layout.addWidget(self.datalog, 1)
        splitter.addWidget(datalog_page)

        self.view_tabs = QTabWidget()
        map_page = QWidget()
        map_layout = QVBoxLayout(map_page)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self.wafer = WaferMapWidget()
        self.layout_label = QLabel("footprint: -")
        map_layout.addWidget(self.layout_label)
        map_layout.addWidget(self.wafer, 3)
        self.bin_bar = BinBarWidget()
        map_layout.addWidget(self.bin_bar)
        export = QPushButton("Export wafer map PNG")
        export.clicked.connect(self.export_wafer_map)
        map_layout.addWidget(export)
        self.view_tabs.addTab(map_page, "Wafer")

        summary_page = QWidget()
        summary_layout = QVBoxLayout(summary_page)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_row = QHBoxLayout()
        summary_row.addWidget(QLabel("group by"))
        self.group_by = GroupByBox()
        self.group_by.changed.connect(self._fill_summary)
        summary_row.addWidget(self.group_by)
        self.summary_count = QLabel("0 rows")
        summary_row.addWidget(self.summary_count)
        summary_row.addStretch(1)
        summary_layout.addLayout(summary_row)
        self.summary_table = QTableWidget(0, len(SUMMARY_COLUMNS))
        self.summary_table.setHorizontalHeaderLabels(SUMMARY_COLUMNS)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.summary_table.setAlternatingRowColors(True)
        configure_table(self.summary_table)
        self.summary_table.setColumnWidth(1, 140)
        self.summary_table.setColumnWidth(4, 140)
        self.summary_table.itemSelectionChanged.connect(self._paint_summary_charts)
        _mono(self.summary_table)
        summary_layout.addWidget(self.summary_table, 2)
        chart_host = QHBoxLayout()
        self.summary_histogram = HistogramWidget()
        self.summary_trend = TrendWidget()
        self.summary_probability = ProbabilityWidget()
        for title, widget in (("histogram", self.summary_histogram),
                              ("trend", self.summary_trend),
                              ("probability", self.summary_probability)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(widget, 1)
            chart_host.addLayout(column, 1)
        summary_layout.addLayout(chart_host, 1)
        self.view_tabs.addTab(summary_page, "Summary")
        self._summary_keys: list[tuple[int, int | None, str | None]] = []

        shmoo_page = QWidget()
        self.shmoo_page = shmoo_page
        shmoo_layout = QVBoxLayout(shmoo_page)
        shmoo_layout.setContentsMargins(0, 0, 0, 0)
        shmoo_row = QHBoxLayout()
        self.shmoo_vdd = QSpinBox()
        self.shmoo_vdd.setRange(2, 24)
        self.shmoo_vdd.setValue(4)
        self.shmoo_period = QSpinBox()
        self.shmoo_period.setRange(2, 24)
        self.shmoo_period.setValue(4)
        self.shmoo_cycles = QSpinBox()
        self.shmoo_cycles.setRange(1, 10000)
        self.shmoo_cycles.setValue(16)
        shmoo_run = QPushButton("Run shmoo")
        shmoo_run.clicked.connect(self.run_shmoo_once)
        shmoo_png = QPushButton("PNG")
        shmoo_png.clicked.connect(self.export_shmoo_png)
        shmoo_csv = QPushButton("CSV")
        shmoo_csv.clicked.connect(self.export_shmoo_csv)
        for label, widget in (("V", self.shmoo_vdd), ("T", self.shmoo_period),
                              ("cyc", self.shmoo_cycles)):
            shmoo_row.addWidget(QLabel(label))
            shmoo_row.addWidget(widget)
        for widget in (shmoo_run, shmoo_png, shmoo_csv):
            shmoo_row.addWidget(widget)
        shmoo_layout.addLayout(shmoo_row)
        self.shmoo = ShmooWidget()
        shmoo_layout.addWidget(self.shmoo, 1)
        self.view_tabs.addTab(shmoo_page, "Shmoo")

        self.tray = TrayWidget()
        self.view_tabs.addTab(self.tray, "Tray")
        splitter.addWidget(self.view_tabs)
        splitter.setSizes([620, 420])
        layout.addWidget(splitter, 1)

        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("log"))
        self.log_level = QComboBox()
        self.log_level.addItems(["ALL", "INFO", "ERROR"])
        self.log_level.currentTextChanged.connect(self._on_log_level)
        log_row.addWidget(self.log_level)
        log_row.addStretch(1)
        layout.addLayout(log_row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        _mono(self.log_view)
        layout.addWidget(self.log_view)

    # ------------------------------------------------------------------ state
    def set_session(self, session: Session | None) -> None:
        """Populate the controls from a loaded session."""
        self.stage_box.clear()
        if session is None:
            return
        try:
            info = session.info()
        except ConfigProblem:
            return
        self.stage_box.addItems(info.stages)
        self.stage_box.setCurrentText(info.stage)
        self.sites_spin.setValue(info.sites)
        self.seed_spin.setValue(int(session.loaded.run.seed))
        self.site_filter.clear()
        self.site_filter.addItem("all sites", None)
        for site in range(1, info.sites + 1):
            self.site_filter.addItem(f"site {site}", site)
        loaded = session.loaded
        wafer = getattr(loaded, "wafer", None)
        if wafer is not None:
            self.wafer_size = float(wafer.diameter_mm)
            self.die_size = float(max(wafer.die_size_mm))
        self._rebuild_site_checks(info.sites, set(loaded.run.disabled_sites))
        self.session_path = str(session.path)

    def _rebuild_site_checks(self, count: int, disabled: set[int]) -> None:
        while self.site_checks:
            box = self.site_checks.pop()
            self.site_row.removeWidget(box)
            box.deleteLater()
        for site in range(1, int(count) + 1):
            box = QCheckBox(f"S{site}")
            box.setChecked(site not in disabled)
            self.site_checks.append(box)
            self.site_row.addWidget(box)

    # ------------------------------------------------------------------ slots
    def start_run(self) -> None:
        """Start the configured run (needs a session)."""
        path = self.session_path
        if not path:
            QMessageBox.information(self, "run", "open a session first")
            return
        session = Session(path)
        extra = dict(self.extra_overrides)
        disabled = [index + 1 for index, box in enumerate(self.site_checks) if not box.isChecked()]
        if disabled:
            extra["run.disabled_sites"] = disabled
        options = RunOptions(
            stage=self.stage_box.currentText() or None,
            sites=self.sites_spin.value() or None,
            seed=self.seed_spin.value() or None,
            output_dir=self.output_dir,
            extra=extra,
        )
        self.clear_results()
        try:
            handle = RunHandle(session, options, events=EventBus())
            handle.prepare()
        except ConfigProblem as exc:
            QMessageBox.warning(self, "configuration", str(exc))
            return
        for test_num in self.breakpoints:
            handle.set_breakpoint(test_num)
        self.handle = handle
        self.vm.detach()
        self.vm.attach(handle.events)
        self.log_view.appendPlainText("run started")
        prober = getattr(session.loaded, "prober", None)
        if prober is not None:
            self.log_view.appendPlainText(
                f"prober layout {prober.layout.cols}x{prober.layout.rows} "
                f"scan {prober.scan}  sites {self.sites_spin.value()}"
            )
        handle.start()

    def clear_results(self) -> None:
        """Drop every result from the previous run so the next one starts empty."""
        self.vm.detach()
        if self.handle is not None:
            self.handle.stop()
            self.handle = None
        self.vm.reset()
        self.datalog.setRowCount(0)
        self.wafer.set_data({}, wafer_size_mm=self.wafer_size, die_size_mm=self.die_size, details={})
        self.bin_bar.set_data([])
        self.shmoo.set_data({})
        self.tray.set_data({}, {})
        self.log_view.clear()
        self.progress.setValue(0)
        self.unit_label.setText("unit: -")
        self.parts_label.setText("parts: 0")
        self.yield_label.setText("yield: -")
        self.bins_label.setText("bins: 0")
        self.uph_label.setText("uph: -")
        self._summary_keys = []
        self.summary_table.setRowCount(0)
        self.datalog_count.setText("0 rows")
        self.summary_count.setText("0 rows")
        self.layout_label.setText("footprint: -")
        self.summary_histogram.set_bins([])
        self.summary_trend.set_values([])
        self.summary_probability.set_points([])
        self.summary_histogram.set_limits(None, None)
        self.summary_trend.set_limits(None, None)
        self.summary_probability.set_limits(None, None)

    def pause_run(self) -> None:
        """Pause the run."""
        if self.handle is not None:
            self.handle.pause()

    def resume_run(self) -> None:
        """Resume a paused run."""
        if self.handle is not None:
            self.handle.resume()

    def stop_run(self) -> None:
        """Abort the run."""
        if self.handle is not None:
            self.handle.stop()

    def step_run(self) -> None:
        """Execute one more test item, then pause."""
        if self.handle is not None:
            self.handle.step()

    def _add_breakpoint(self) -> None:
        test_num = int(self.bp_spin.value())
        self.breakpoints.add(test_num)
        if self.handle is not None:
            self.handle.set_breakpoint(test_num)
        self._show_breakpoints()

    def _clear_breakpoints(self) -> None:
        self.breakpoints.clear()
        if self.handle is not None:
            self.handle.clear_breakpoint()
        self._show_breakpoints()

    def _show_breakpoints(self) -> None:
        if not self.breakpoints:
            self.bp_label.setText("breakpoints: none")
            return
        text = ", ".join(str(num) for num in sorted(self.breakpoints))
        self.bp_label.setText(f"breakpoints: {text}")

    def _on_datalog_select(self) -> None:
        row = self.datalog.currentRow()
        item = self.datalog.item(row, 2) if row >= 0 else None
        if item is None:
            return
        try:
            test_num = int(float(item.text()))
        except ValueError:
            return
        self.bp_spin.setValue(test_num)
        for index in range(self.summary_table.rowCount()):
            item = self.summary_table.item(index, 0)
            if item is not None and item.data(Qt.UserRole) == test_num:
                self.summary_table.setCurrentCell(index, 0)
                return

    def _on_log_level(self, level: str) -> None:
        self._log_level = level
        self._refill_log()

    def _refill_log(self) -> None:
        self.log_view.clear()
        for entry in self.vm.logs:
            if self._log_level != "ALL" and entry.level != self._log_level:
                continue
            self.log_view.appendPlainText(f"{entry.level}: {entry.message}")

    def run_shmoo_once(self) -> None:
        """Characterize the current session on one site, off the wafer loop."""
        path = self.session_path
        if not path:
            QMessageBox.information(self, "shmoo", "open a session first")
            return
        params = {
            "vdd_steps": int(self.shmoo_vdd.value()),
            "period_steps": int(self.shmoo_period.value()),
            "cycles": int(self.shmoo_cycles.value()),
        }

        def work() -> None:
            try:
                data = run_shmoo(Session(path), params=params)
            except Exception as exc:  # noqa: BLE001 - reported on the GUI thread
                self.shmooReady.emit({"error": str(exc)})
                return
            self.shmooReady.emit(data)

        self.log_view.appendPlainText("shmoo started")
        threading.Thread(target=work, name="pyate-shmoo", daemon=True).start()

    def _on_shmoo_ready(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        if data.get("error"):
            self.log_view.appendPlainText(f"shmoo: {data['error']}")
            return
        self.vm.set_shmoo(data)
        self.shmoo.set_data(data)
        self.view_tabs.setCurrentWidget(self.shmoo_page)
        self.log_view.appendPlainText(f"shmoo pass rate {data.get('pass_rate')}")

    def export_shmoo_png(self) -> None:
        """Save the shmoo plot as a PNG."""
        path, _ = QFileDialog.getSaveFileName(self, "Export shmoo", "shmoo.png", "PNG (*.png)")
        if path and self.shmoo.export_png(path):
            self.log_view.appendPlainText(f"shmoo png saved to {path}")

    def export_shmoo_csv(self) -> None:
        """Save the shmoo matrix as CSV."""
        path, _ = QFileDialog.getSaveFileName(self, "Export shmoo", "shmoo.csv", "CSV (*.csv)")
        if path and self.shmoo.export_csv(path):
            self.log_view.appendPlainText(f"shmoo csv saved to {path}")

    def _fill_summary(self) -> None:
        """Rebuild the test summary for the checked group dimensions."""
        rows = self.vm.summary_rows(groups=self.group_by.groups())
        keys = [(row.test_num, row.site, row.wafer, row.part) for row in rows]
        selected = self.summary_table.currentRow()
        self.summary_table.blockSignals(True)
        self.summary_table.setSortingEnabled(False)
        if self.summary_table.rowCount() != len(rows):
            self.summary_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            cells = summary_cells(row)
            sorts = (
                row.test_num, row.name,
                -1 if row.site is None else row.site,
                "" if row.wafer is None else row.wafer,
                "" if row.part is None else row.part,
                row.count, row.passed, row.failed,
                row.mean, row.stdev, row.minimum, row.maximum,
                row.lo, row.hi, row.cp, row.cpk,
            )
            for column, text in enumerate(cells):
                _put_cell(self.summary_table, index, column, text, sorts[column])
            self.summary_table.item(index, 0).setData(Qt.UserRole + 1, row.test_num)
            self.summary_table.item(index, 2).setData(Qt.UserRole + 1, row.site)
            self.summary_table.item(index, 3).setData(Qt.UserRole + 1, row.wafer)
            self.summary_table.item(index, 4).setData(Qt.UserRole + 1, row.part)
        if rows and not (0 <= selected < len(rows)):
            self.summary_table.setCurrentCell(0, 0)
        self._summary_keys = keys
        self.summary_count.setText(f"{len(rows)} rows")
        self.summary_table.setSortingEnabled(True)
        self.summary_table.blockSignals(False)
        self._paint_summary_charts()

    def _paint_summary_charts(self) -> None:
        row_index = self.summary_table.currentRow()
        test_item = self.summary_table.item(row_index, 0) if row_index >= 0 else None
        site_item = self.summary_table.item(row_index, 2) if row_index >= 0 else None
        wafer_item = self.summary_table.item(row_index, 3) if row_index >= 0 else None
        part_item = self.summary_table.item(row_index, 4) if row_index >= 0 else None
        if test_item is None:
            self.summary_histogram.set_bins([])
            self.summary_trend.set_values([])
            self.summary_probability.set_points([])
            self.summary_histogram.set_limits(None, None)
            self.summary_trend.set_limits(None, None)
            self.summary_probability.set_limits(None, None)
            return
        test_num = int(test_item.data(Qt.UserRole + 1))
        site_id = None if site_item is None else site_item.data(Qt.UserRole + 1)
        if site_id is not None:
            site_id = int(site_id)
        wafer_id = None if wafer_item is None else wafer_item.data(Qt.UserRole + 1)
        part_id = None if part_item is None else part_item.data(Qt.UserRole + 1)
        lo, hi = self.vm.limits_of(test_num, site_id, wafer_id, part_id)
        self.summary_histogram.set_limits(lo, hi)
        self.summary_trend.set_limits(lo, hi)
        self.summary_probability.set_limits(lo, hi)
        self.summary_histogram.set_bins(
            self.vm.histogram(test_num, site=site_id, wafer=wafer_id, part=part_id))
        self.summary_trend.set_values(self.vm.trend(test_num, site_id, wafer_id, part_id))
        self.summary_probability.set_points(
            self.vm.probability(test_num, site_id, wafer_id, part_id))

    def export_wafer_map(self) -> None:
        """Save the current wafer map as PNG."""
        path, _ = QFileDialog.getSaveFileName(self, "Export wafer map",
                                              "wafer_map.png", "PNG (*.png)")
        if path and self.wafer.export_png(path):
            self.log_view.appendPlainText(f"wafer map saved to {path}")

    # ------------------------------------------------------------------- tick
    def _tick(self) -> None:
        if not self.vm.should_render():
            return
        snapshot = self.vm.snapshot()
        self.unit_label.setText(f"unit: {snapshot['unit'] or '-'}")
        self.parts_label.setText(f"parts: {snapshot['parts']}")
        self.yield_label.setText(f"yield: {snapshot['yield_pct']:.2f} %")
        self.bins_label.setText(f"bins: {len(snapshot['bins'])}")
        self.uph_label.setText(f"uph: {snapshot.get('uph', 0):.1f}")
        if snapshot["touchdowns"]:
            pct = 100.0 * snapshot["touchdown"] / snapshot["touchdowns"]
            self.progress.setValue(int(min(100, max(0, pct))))
        elif snapshot["finished"]:
            self.progress.setValue(100)
        self._refresh_rows()
        self.bin_bar.set_data(self.vm.bin_rows())
        self.wafer.set_data(self.vm.dies,
                            wafer_size_mm=self.wafer_size,
                            die_size_mm=self.die_size,
                            details=self.vm.die_info)
        plot = self.vm.shmoo()
        self.shmoo.set_data(plot if plot.get("pass") else {})
        self._show_footprint()
        self.tray.set_data(self.vm.touch, self.vm.site_state)
        self._fill_summary()
        for entry in self.vm.take_logs():
            if self._log_level != "ALL" and entry.level != self._log_level:
                continue
            self.log_view.appendPlainText(f"{entry.level}: {entry.message}")
        if snapshot["finished"]:
            self.log_view.appendPlainText(
                f"run finished: {snapshot['passed']}/{snapshot['parts']} passed")

    def _on_site_filter(self) -> None:
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        site = self.site_filter.currentData()
        rows = [row for row in self.vm.rows_for_site(site)]
        colors = bins_colors()
        self.datalog.setSortingEnabled(False)
        self.datalog.setRowCount(len(rows))
        numeric = {0, 2, 4, 6, 7}
        for index, row in enumerate(rows):
            values = (
                (row.site, row.site),
                (row.part, row.part),
                (row.test_num, row.test_num),
                (row.name, row.name),
                (_fmt_num(row.value), row.value),
                (row.unit, row.unit),
                (_fmt_num(row.lo), row.lo),
                (_fmt_num(row.hi), row.hi),
                ("PASS" if row.passed else "FAIL", 0 if row.passed else 1),
            )
            for col, (text, sort_value) in enumerate(values):
                item = _put_cell(self.datalog, index, col, str(text), sort_value)
                if col in numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 8:
                    item.setForeground(QColor(colors.get(1 if row.passed else 2, "#2e9e5b")))
                elif not row.passed:
                    item.setForeground(QColor(colors.get(2, "#c0392b")))
        self.datalog.setSortingEnabled(True)
        self.datalog_count.setText(f"{len(rows)} rows")
        if rows and not self.datalog.horizontalHeader().isSortIndicatorShown():
            self.datalog.scrollToBottom()

    def _show_footprint(self) -> None:
        """Show the touchdown shape the prober is actually using."""
        touch = self.vm.touch
        layout = touch.get("layout") or ()
        if len(layout) != 2:
            return
        scan = touch.get("scan") or "-"
        configured = touch.get("configured_layout") or ()
        text = f"footprint {layout[0]}×{layout[1]}  scan {scan}"
        if len(configured) == 2 and tuple(configured) != tuple(layout):
            text += (f"  (prober {configured[0]}×{configured[1]} "
                     f"trimmed to {touch.get('sites', '')} sites)")
        self.layout_label.setText(text)


# ------------------------------------------------------------------ batch panel
class BatchPanel(QWidget):
    """Load and run a ``batch.yaml`` and watch the job states."""

    def __init__(self, vm: BatchViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to batch.yaml")
        browse = QPushButton("Open...")
        browse.clicked.connect(self._browse)
        load = QPushButton("Load")
        load.clicked.connect(self._load)
        self.start_btn = QPushButton("Run batch")
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = QPushButton("Open output")
        self.open_btn.clicked.connect(self.open_output)
        row.addWidget(self.path_edit)
        for widget in (browse, load, self.start_btn, self.cancel_btn, self.open_btn):
            row.addWidget(widget)
        row.addWidget(QLabel("workers:"))
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(2)
        row.addWidget(self.workers)
        layout.addLayout(row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["job", "status", "parts", "yield %", "duration s", "error"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(
            ["job", "status", "parts", "yield %", "duration s", "error"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.status = QLabel("no batch loaded")
        layout.addWidget(self.status)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open batch", "",
                                              "Batch (*.yaml *.yml)")
        if path:
            self.path_edit.setText(path)
            self._load()

    def _load(self) -> None:
        rows = self.vm.load(self.path_edit.text().strip())
        self._fill(rows)
        self.status.setText(self.vm.error or f"{len(rows)} job(s)")

    def start(self) -> bool:
        """Start the batch in the background."""
        if not self.vm.rows:
            self._load()
        if not self.vm.start(workers=self.workers.value()):
            self.status.setText(self.vm.error or "nothing to run")
            return False
        self.status.setText("batch running...")
        return True

    def cancel(self) -> None:
        """Cancel pending jobs."""
        self.vm.cancel()

    def _tick(self) -> None:
        if self.vm.runner is None:
            return
        self._fill(self.vm.poll())
        self.status.setText(self.vm.aggregate_text() if not self.vm.running
                            else "batch running...")

    def _fill(self, rows: list[Any]) -> None:
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (row.job_id, row.status, row.parts, round(row.yield_pct, 2),
                      round(row.duration_s, 1), row.error)
            for col, value in enumerate(values):
                item = QTableWidgetItem("-" if value is None else _fmt_num(value))
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 1:
                    colour = {"DONE": "#2e9e5b", "FAILED": "#c0392b",
                              "CANCELLED": "#e67e22"}.get(str(row.status).upper())
                    if colour:
                        item.setForeground(QColor(colour))
                self.table.setItem(index, col, item)
            self.table.item(index, 0).setData(Qt.UserRole, row.output_dir)

    def open_output(self) -> None:
        """Open the selected job's output directory in the file manager."""
        row = self.table.currentRow()
        path = ""
        if row >= 0:
            item = self.table.item(row, 0)
            path = "" if item is None else str(item.data(Qt.UserRole) or "")
        if not path or not Path(path).is_dir():
            self.status.setText("select a job that already has an output directory")
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - user asked to open the folder
        else:
            self.status.setText(path)


# ---------------------------------------------------------------- pattern panel
class PatternPanel(QWidget):
    """Run a ``.pat`` file against a DUT model (pattern debugger)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        box = QGroupBox("pattern debugger")
        form = QFormLayout(box)
        form.setContentsMargins(12, 6, 12, 10)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(6)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to .pat")
        browse = QPushButton("Open...")
        browse.clicked.connect(self._browse)
        file_layout.addWidget(self.path_edit, 1)
        file_layout.addWidget(browse)
        form.addRow("pattern:", file_row)

        self.dut_box = QComboBox()
        self.period = QDoubleSpinBox()
        self.period.setRange(1.0, 1000.0)
        self.period.setValue(20.0)
        self.period.setSuffix(" ns")
        self.vdd = QDoubleSpinBox()
        self.vdd.setRange(0.5, 5.0)
        self.vdd.setSingleStep(0.1)
        self.vdd.setDecimals(2)
        self.vdd.setValue(3.3)
        self.vdd.setSuffix(" V")
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 1_000_000)
        self.cycles.setValue(100_000)
        self.cycles.setSingleStep(1000)
        form.addRow("DUT model:", self.dut_box)
        form.addRow("period:", self.period)
        form.addRow("VDD:", self.vdd)
        form.addRow("max cycles:", self.cycles)
        # a QComboBox is a few pixels taller than a spin box by default; pin
        # every field to one height so the form lines up
        for field in (self.dut_box, self.period, self.vdd, self.cycles):
            field.setFixedHeight(26)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch(1)
        self.run_btn = QPushButton("Run pattern")
        self.run_btn.clicked.connect(self.run_pattern)
        action_layout.addWidget(self.run_btn)
        form.addRow("", action_row)
        layout.addWidget(box)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("pattern results (vectors, cycles, first failing cycle)")
        _mono(self.output)
        layout.addWidget(self.output, 1)
        self.compare = PatternCompareWidget()
        self.compare.setMinimumHeight(140)
        layout.addWidget(self.compare, 1)
        self.refresh_models()

    def refresh_models(self) -> None:
        """Fill the DUT model list from the registry."""
        from ..api import available_models

        current = self.dut_box.currentText()
        self.dut_box.clear()
        models = available_models()
        self.dut_box.addItems(models)
        if current in models:
            self.dut_box.setCurrentText(current)
        elif "composite" in models:
            self.dut_box.setCurrentText("composite")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open pattern", "",
                                              "Pattern (*.pat *.txt)")
        if path:
            self.path_edit.setText(path)

    def run_pattern(self) -> None:
        """Execute the pattern and show the first failing cycle."""
        from ..api import run_pattern

        path = self.path_edit.text().strip()
        if not path or not Path(path).is_file():
            QMessageBox.information(self, "pattern", "select a .pat file first")
            return
        try:
            report = run_pattern(
                path,
                model=self.dut_box.currentText() or "composite",
                vdd=self.vdd.value(),
                period_ns=self.period.value(),
                max_cycles=self.cycles.value(),
            )
        except Exception as exc:  # noqa: BLE001 - shown in the panel
            self.output.setPlainText(f"{type(exc).__name__}: {exc}")
            return
        lines = [
            f"pattern : {report.pattern} ({report.vectors} vectors)",
            f"cycles  : {report.cycles}   elapsed {report.elapsed_ms:.2f} ms",
            f"result  : {'PASS' if report.passed else 'FAIL'}",
        ]
        first = report.first_fail
        if first is not None:
            lines.append(f"first fail: cycle {first.cycle} line {first.line} "
                         f"pins={','.join(first.pins)}")
            lines.append(f"  expected {first.expected}")
            lines.append(f"  actual   {first.actual}")
            self.compare.set_fail(first.expected, first.actual, list(first.pins))
        else:
            self.compare.set_fail({}, {}, [])
        self.output.setPlainText("\n".join(lines))
