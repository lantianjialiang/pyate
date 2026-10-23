"""Form + text editor for one configuration file.

The form is generated from the pydantic JSON schema the API returns. Lists of
objects become tables; everything else is a labelled field. The text tab is
YAML, or CSV when the file on disk is a limits table. Saving goes through
``Session`` so a failed validation never overwrites the file.
"""

from __future__ import annotations

import copy
import csv
import io
from typing import Any

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

__all__ = ["FileEditor"]

_LIMIT_COLUMNS = [
    "test_num", "name", "lo", "hi", "unit", "scale", "stage", "temp_min", "temp_max",
]


def _deref(schema: dict[str, Any] | None, root: dict[str, Any]) -> dict[str, Any]:
    """Follow ``$ref`` / ``anyOf`` / ``allOf`` one step."""
    if not schema:
        return {}
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return dict(root.get("$defs", {}).get(name) or {})
    if "allOf" in schema:
        merged: dict[str, Any] = {"type": "object", "properties": {}}
        for part in schema["allOf"]:
            resolved = _deref(part, root)
            merged["properties"].update(resolved.get("properties") or {})
        return merged
    if "anyOf" in schema:
        for part in schema["anyOf"]:
            resolved = _deref(part, root)
            if resolved.get("type") != "null":
                return resolved
        return {}
    return schema


def _flatten_columns(item_schema: dict[str, Any], root: dict[str, Any]) -> list[tuple[str, str]]:
    """Table columns. Nested scalar objects become ``parent.child`` headers."""
    resolved = _deref(item_schema, root)
    columns: list[tuple[str, str]] = []
    for name, prop in (resolved.get("properties") or {}).items():
        child = _deref(prop, root)
        kind = child.get("type")
        if kind == "object" and child.get("properties"):
            for sub, sub_schema in child["properties"].items():
                columns.append((f"{name}.{sub}", _deref(sub_schema, root).get("type") or "string"))
        else:
            columns.append((name, kind or "string"))
    return columns


def _dig(row: dict[str, Any], dotted: str) -> Any:
    cursor: Any = row
    for part in dotted.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _put(row: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = row
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def _coerce(text: str, kind: str) -> Any:
    stripped = text.strip()
    if stripped == "":
        return None
    if kind == "integer":
        return int(float(stripped))
    if kind == "number":
        return float(stripped)
    if kind == "boolean":
        return stripped.lower() in ("1", "true", "yes")
    return stripped


class FileEditor(QWidget):
    """Edit one config document as a generated form or as text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = ""
        self.path = ""
        self.kind = "yaml"
        self._schema: dict[str, Any] = {}
        self._data: Any = {}
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.title = QLabel("select a file in the tree")
        self.title.setWordWrap(True)
        header.addWidget(self.title, 1)
        self.save_btn = QPushButton("Save file")
        self.save_btn.setEnabled(False)
        header.addWidget(self.save_btn)
        layout.addLayout(header)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.tabs = QTabWidget()
        self.form_host = QScrollArea()
        self.form_host.setWidgetResizable(True)
        self.form_body = QWidget()
        self.form_layout = QVBoxLayout(self.form_body)
        self.form_host.setWidget(self.form_body)
        self.tabs.addTab(self.form_host, "Form")

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("YAML or CSV")
        font = self.text.font()
        font.setFamily("Consolas")
        self.text.setFont(font)
        self.tabs.addTab(self.text, "YAML")
        self.tabs.currentChanged.connect(self._on_tab)
        layout.addWidget(self.tabs, 1)

    def load_document(self, key: str, path: str, kind: str, text: str,
                      data: Any, schema: dict[str, Any]) -> None:
        """Show ``data`` (and its schema) for ``key``."""
        self.key = key
        self.path = path
        self.kind = kind
        self._schema = dict(schema or {})
        self._data = copy.deepcopy(data)
        self.save_btn.setEnabled(True)
        self.title.setText(f"{key}  {path}")
        self.tabs.setTabText(1, "CSV" if kind == "csv" else "YAML")
        self._syncing = True
        self.text.setPlainText(text)
        self._syncing = False
        self._rebuild_form()
        self.status.setText("")

    def clear(self) -> None:
        """Forget the current document."""
        self.key = ""
        self.path = ""
        self._data = {}
        self._schema = {}
        self.save_btn.setEnabled(False)
        self.title.setText("select a file in the tree")
        self.text.clear()
        self._clear_form()

    def payload(self) -> tuple[str, Any]:
        """``('data', dict)`` from the form, or ``('text', str)`` from the text tab."""
        if self.tabs.currentWidget() is self.text:
            return ("text", self.text.toPlainText())
        return ("data", self._collect())

    # ------------------------------------------------------------------ tabs
    def _on_tab(self, index: int) -> None:
        if self._syncing or not self.key:
            return
        if index == 0:
            self._pull_text()
        else:
            self._push_text()

    def _push_text(self) -> None:
        data = self._collect()
        self._syncing = True
        if self.kind == "csv":
            self.text.setPlainText(_limits_csv(data.get("limits") if isinstance(data, dict) else data))
        else:
            self.text.setPlainText(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        self._syncing = False

    def _pull_text(self) -> None:
        raw = self.text.toPlainText()
        try:
            if self.kind == "csv":
                self._data = {"limits": _parse_csv(raw)}
            else:
                self._data = yaml.safe_load(raw) or {}
        except Exception as exc:  # noqa: BLE001 - keep the previous form
            self.status.setText(f"text: {exc}")
            return
        self.status.setText("")
        self._rebuild_form()

    # ------------------------------------------------------------------ form
    def _clear_form(self) -> None:
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_form(self) -> None:
        self._clear_form()
        schema = _deref(self._schema, self._schema)
        if not isinstance(self._data, dict):
            note = QLabel("this document is not an object; edit it in the text tab")
            note.setWordWrap(True)
            self.form_layout.addWidget(note)
            self.form_layout.addStretch(1)
            return
        box = QGroupBox(self.key or "document")
        form = QFormLayout(box)
        self._fill_object(form, schema, self._data, self._schema)
        self.form_layout.addWidget(box)
        for name, prop in (schema.get("properties") or {}).items():
            child = _deref(prop, self._schema)
            if child.get("type") == "array":
                item_schema = child.get("items") or {}
                resolved_item = _deref(item_schema, self._schema)
                if resolved_item.get("type") == "object" or resolved_item.get("properties"):
                    rows = self._data.get(name) or []
                    if isinstance(rows, list):
                        self.form_layout.addWidget(self._make_table(name, rows, item_schema))
        self.form_layout.addStretch(1)

    def _fill_object(self, form: QFormLayout, schema: dict[str, Any],
                     data: dict[str, Any], root: dict[str, Any]) -> None:
        for name, prop in (schema.get("properties") or {}).items():
            child = _deref(prop, root)
            kind = child.get("type")
            if kind == "array":
                item = _deref(child.get("items") or {}, root)
                if item.get("type") in ("string", "number", "integer") or "enum" in item:
                    editor = QPlainTextEdit()
                    editor.setMaximumHeight(72)
                    values = data.get(name) or []
                    if isinstance(values, list):
                        editor.setPlainText("\n".join(str(value) for value in values))
                    editor.setObjectName(f"lines:{name}")
                    form.addRow(name, editor)
                continue
            if kind == "object" and child.get("properties"):
                nested = data.get(name) if isinstance(data.get(name), dict) else {}
                group = QGroupBox(name)
                inner = QFormLayout(group)
                self._fill_object(inner, child, nested, root)
                form.addRow(group)
                continue
            if kind == "object":
                editor = QPlainTextEdit()
                editor.setMaximumHeight(80)
                editor.setObjectName(f"yaml:{name}")
                value = data.get(name)
                if value not in (None, {}, []):
                    editor.setPlainText(yaml.safe_dump(value, sort_keys=False))
                form.addRow(name, editor)
                continue
            form.addRow(name, self._scalar_widget(name, child, data.get(name)))

    def _scalar_widget(self, name: str, schema: dict[str, Any], value: Any) -> QWidget:
        if "enum" in schema:
            box = QComboBox()
            box.setObjectName(f"enum:{name}")
            for option in schema["enum"]:
                box.addItem(str(option), option)
            if value is not None:
                index = box.findData(value)
                if index < 0:
                    index = box.findText(str(value))
                if index >= 0:
                    box.setCurrentIndex(index)
            return box
        if schema.get("type") == "boolean":
            box = QCheckBox()
            box.setObjectName(f"bool:{name}")
            box.setChecked(bool(value))
            return box
        edit = QLineEdit()
        kind = schema.get("type") or "string"
        edit.setObjectName(f"{kind}:{name}")
        if value is not None:
            edit.setText(str(value))
        return edit

    def _make_table(self, name: str, rows: list[Any], item_schema: dict[str, Any]) -> QGroupBox:
        columns = _flatten_columns(item_schema, self._schema)
        group = QGroupBox(name)
        layout = QVBoxLayout(group)
        table = QTableWidget(len(rows), len(columns))
        table.setObjectName(f"table:{name}")
        table.setHorizontalHeaderLabels([column for column, _kind in columns])
        table.verticalHeader().setVisible(False)
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for col_index, (column, _kind) in enumerate(columns):
                value = _dig(row, column)
                item = QTableWidgetItem("" if value is None else str(value))
                table.setItem(row_index, col_index, item)
        layout.addWidget(table)
        buttons = QHBoxLayout()
        add = QPushButton("Add row")
        remove = QPushButton("Remove row")
        add.clicked.connect(lambda: table.insertRow(table.rowCount()))
        remove.clicked.connect(lambda: table.removeRow(table.currentRow())
                               if table.currentRow() >= 0 else None)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return group

    def _collect(self) -> Any:
        """Read the form back into a document, keeping keys the form does not show."""
        if not isinstance(self._data, dict):
            return self._data
        data = copy.deepcopy(self._data)
        schema = _deref(self._schema, self._schema)
        self._read_object(self.form_body, schema, data, self._schema)
        for name, prop in (schema.get("properties") or {}).items():
            child = _deref(prop, self._schema)
            if child.get("type") != "array":
                continue
            table = self.form_body.findChild(QTableWidget, f"table:{name}")
            if table is None:
                continue
            columns = _flatten_columns(child.get("items") or {}, self._schema)
            rows: list[dict[str, Any]] = []
            original = data.get(name) if isinstance(data.get(name), list) else []
            for row_index in range(table.rowCount()):
                base = copy.deepcopy(original[row_index]) if row_index < len(original) and isinstance(original[row_index], dict) else {}
                for col_index, (column, kind) in enumerate(columns):
                    item = table.item(row_index, col_index)
                    text = "" if item is None else item.text()
                    _put(base, column, _coerce(text, kind))
                rows.append(base)
            data[name] = rows
        self._data = data
        return data

    def _read_object(self, root: QWidget, schema: dict[str, Any],
                     data: dict[str, Any], defs: dict[str, Any]) -> None:
        for name, prop in (schema.get("properties") or {}).items():
            child = _deref(prop, defs)
            kind = child.get("type")
            if kind == "object" and child.get("properties"):
                nested = data.get(name)
                if not isinstance(nested, dict):
                    nested = {}
                    data[name] = nested
                self._read_object(root, child, nested, defs)
                continue
            if kind == "array":
                editor = root.findChild(QPlainTextEdit, f"lines:{name}")
                if editor is None:
                    continue
                item = _deref(child.get("items") or {}, defs)
                item_kind = item.get("type") or "string"
                lines = [line.strip() for line in editor.toPlainText().splitlines() if line.strip()]
                data[name] = [_coerce(line, item_kind) for line in lines]
                continue
            if kind == "object":
                editor = root.findChild(QPlainTextEdit, f"yaml:{name}")
                if editor is None:
                    continue
                raw = editor.toPlainText().strip()
                if not raw:
                    data[name] = {}
                    continue
                data[name] = yaml.safe_load(raw)
                continue
            widget = self._find_scalar(root, name)
            if widget is None:
                continue
            data[name] = self._read_scalar(widget, child)

    def _find_scalar(self, root: QWidget, name: str) -> QWidget | None:
        for prefix in ("enum", "bool", "integer", "number", "string"):
            found = root.findChild(QWidget, f"{prefix}:{name}")
            if found is not None:
                return found
        return None

    @staticmethod
    def _read_scalar(widget: QWidget, schema: dict[str, Any]) -> Any:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            stored = widget.currentData()
            return widget.currentText() if stored is None else stored
        text = widget.text() if isinstance(widget, QLineEdit) else ""
        return _coerce(text, schema.get("type") or "string")


def _limits_csv(rows: Any) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_LIMIT_COLUMNS)
    if not isinstance(rows, list):
        return buffer.getvalue()
    for row in rows:
        if not isinstance(row, dict):
            continue
        writer.writerow([row.get(column, "") if row.get(column) is not None else ""
                         for column in _LIMIT_COLUMNS])
    return buffer.getvalue()


def _parse_csv(text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        clean = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
        if not clean.get("test_num"):
            continue
        parsed.append({
            "test_num": int(clean["test_num"]),
            "name": clean.get("name", ""),
            "lo": float(clean["lo"]) if clean.get("lo") else None,
            "hi": float(clean["hi"]) if clean.get("hi") else None,
            "unit": clean.get("unit", ""),
            "scale": int(clean["scale"]) if clean.get("scale") else 0,
            "stage": clean.get("stage") or "*",
            "temp_min": float(clean["temp_min"]) if clean.get("temp_min") else -999.0,
            "temp_max": float(clean["temp_max"]) if clean.get("temp_max") else 999.0,
        })
    return parsed
