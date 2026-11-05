"""STDF file viewer. The test table and charts are the Run summary, plus file stats."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..api import inspect_stdf
from .panels import SUMMARY_COLUMNS, GroupByBox, _mono, _put_cell, configure_table, summary_cells
from .viewmodels.run_vm import RunViewModel
from .widgets import HistogramWidget, ProbabilityWidget, TrendWidget

__all__ = ["StdfPanel"]

#: Metrics shown as tiles above the table: (caption, key).
_METRICS = (
    ("parts", "parts"),
    ("good", "good"),
    ("yield", "yield"),
    ("retests", "retests"),
    ("PTR", "ptr"),
    ("FTR", "ftr"),
)


class _MetricTile(QWidget):
    """One big number with a caption underneath."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 22, 0)
        layout.setSpacing(0)
        self.value = QLabel("-")
        self.value.setObjectName("metric")
        self.caption = QLabel(caption)
        self.caption.setObjectName("metricName")
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class StdfPanel(QWidget):
    """Open an STDF file, show its lot statistics, and summarise the tests."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = RunViewModel()
        self._keys: list[tuple[int, int | None, str | None, str | None]] = []
        self._chips: list[tuple[QHBoxLayout, QWidget]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        open_btn = QPushButton("Open STDF...")
        open_btn.clicked.connect(self.open_file)
        bar.addWidget(open_btn)
        bar.addWidget(QLabel("group by"))
        self.group_by = GroupByBox()
        self.group_by.changed.connect(self._fill)
        bar.addWidget(self.group_by)
        self.row_count = QLabel("0 rows")
        bar.addWidget(self.row_count)
        bar.addStretch(1)
        layout.addLayout(bar)

        layout.addWidget(self._build_card())

        self.table = QTableWidget(0, len(SUMMARY_COLUMNS))
        self.table.setHorizontalHeaderLabels(SUMMARY_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        configure_table(self.table)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(4, 140)
        self.table.itemSelectionChanged.connect(self._paint)
        _mono(self.table)
        layout.addWidget(self.table, 3)

        charts = QHBoxLayout()
        self.histogram = HistogramWidget()
        self.trend = TrendWidget()
        self.probability = ProbabilityWidget()
        for title, widget in (("histogram", self.histogram),
                              ("trend", self.trend),
                              ("probability", self.probability)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(widget, 1)
            charts.addLayout(column, 1)
        layout.addLayout(charts, 2)

    # -------------------------------------------------------------------- ui
    def _build_card(self) -> QFrame:
        """File header: path, lot meta, metric tiles and bin chips."""
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)

        self.path_label = QLabel("no STDF file")
        self.path_label.setObjectName("filePath")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.path_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("muted")
        self.meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.meta_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.tiles: dict[str, _MetricTile] = {
            key: _MetricTile(caption) for caption, key in _METRICS
        }
        for tile in self.tiles.values():
            metrics.addWidget(tile)
        metrics.addStretch(1)
        box.addLayout(metrics)

        self.hard_row = self._bin_row("hard bins")
        self.soft_row = self._bin_row("soft bins")
        box.addLayout(self.hard_row)
        box.addLayout(self.soft_row)

        self.records_label = QLabel("")
        self.records_label.setObjectName("muted")
        self.records_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.records_label)
        return card

    def _bin_row(self, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("muted")
        label.setMinimumWidth(72)
        row.addWidget(label)
        row.addStretch(1)
        return row

    def _clear_chips(self) -> None:
        while self._chips:
            row, chip = self._chips.pop()
            row.removeWidget(chip)
            chip.deleteLater()

    def _fill_bins(self, row: QHBoxLayout, entries: list[dict[str, Any]]) -> None:
        for entry in entries[:8]:
            name = str(entry.get("name") or "")
            text = f"bin {entry.get('bin')}"
            if name:
                text += f"  {name}"
            text += f"  {entry.get('count')}"
            chip = QLabel(text)
            chip.setObjectName("chip")
            chip.setToolTip(text)
            row.insertWidget(row.count() - 1, chip)
            self._chips.append((row, chip))
        if len(entries) > 8:
            chip = QLabel(f"+{len(entries) - 8} more")
            chip.setObjectName("chip")
            row.insertWidget(row.count() - 1, chip)
            self._chips.append((row, chip))

    # ------------------------------------------------------------------ file
    def open_file(self) -> None:
        """Ask for an STDF file and load it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STDF", "", "STDF (*.stdf *.stdf.gz *.std *.gz)")
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        """Load an STDF file and rebuild the summary from its PTR/FTR records."""
        report = inspect_stdf(path)
        self.vm.reset()
        for row in report["measurements"]:
            self.vm.add_measurement(
                test_num=int(row["test_num"]),
                name=str(row["name"]),
                site=int(row["site"]),
                value=row["value"],
                passed=bool(row["passed"]),
                wafer=str(row["wafer"] or ""),
                part=str(row.get("part") or ""),
                pin=str(row.get("pin") or ""),
                lo=row.get("lo"),
                hi=row.get("hi"),
            )
        self._show_stats(report)
        self._keys = []
        self._fill()

    def _show_stats(self, report: dict[str, Any]) -> None:
        self.path_label.setText(str(report.get("path", "")))
        self._clear_chips()
        if report.get("errors"):
            self.meta_label.setText("\n".join(str(item) for item in report["errors"]))
            self.path_label.setStyleSheet("color: #c0392b;")
            for tile in self.tiles.values():
                tile.set_value("-")
            self.records_label.setText("")
            return
        self.path_label.setStyleSheet("")
        stats = report["stats"]
        self.meta_label.setText(
            f"lot {stats['lot_id'] or '-'}   part {stats['part_typ'] or '-'}   "
            f"job {stats['job_nam'] or '-'}   node {stats['node_nam'] or '-'}   "
            f"stage {stats['test_cod'] or '-'}   temp {stats['tst_temp'] or '-'}"
        )
        values = {
            "parts": str(stats["parts"]),
            "good": str(stats["good"]),
            "yield": f"{stats['yield_pct']:.2f} %",
            "retests": str(stats["retests"]),
            "ptr": str(stats["ptr_count"]),
            "ftr": str(stats["ftr_count"]),
        }
        for key, text in values.items():
            self.tiles[key].set_value(text)
        self._fill_bins(self.hard_row, list(stats["hard_bins"]))
        self._fill_bins(self.soft_row, list(stats["soft_bins"]))
        records = "   ".join(
            f"{name} {count}" for name, count in stats["record_counts"].items())
        self.records_label.setText(f"records: {records}" if records else "")

    # ----------------------------------------------------------------- table
    def _fill(self) -> None:
        rows = self.vm.summary_rows(groups=self.group_by.groups())
        selected = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        if self.table.rowCount() != len(rows):
            self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            cells = summary_cells(row)
            sorts = (
                row.test_num, row.name,
                -1 if row.site is None else row.site,
                "" if row.wafer is None else row.wafer,
                "" if row.pin is None else row.pin,
                row.count, row.passed, row.failed,
                row.mean, row.stdev, row.minimum, row.maximum,
                row.lo, row.hi, row.cp, row.cpk,
            )
            for column, text in enumerate(cells):
                _put_cell(self.table, index, column, text, sorts[column])
            self.table.item(index, 0).setData(Qt.UserRole + 1, row.test_num)
            self.table.item(index, 2).setData(Qt.UserRole + 1, row.site)
            self.table.item(index, 3).setData(Qt.UserRole + 1, row.wafer)
            self.table.item(index, 4).setData(Qt.UserRole + 1, row.pin)
        if rows and not (0 <= selected < len(rows)):
            self.table.setCurrentCell(0, 0)
        self.row_count.setText(f"{len(rows)} rows")
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._paint()

    def _paint(self) -> None:
        row_index = self.table.currentRow()
        test_item = self.table.item(row_index, 0) if row_index >= 0 else None
        site_item = self.table.item(row_index, 2) if row_index >= 0 else None
        wafer_item = self.table.item(row_index, 3) if row_index >= 0 else None
        pin_item = self.table.item(row_index, 4) if row_index >= 0 else None
        if test_item is None:
            self.histogram.set_bins([])
            self.trend.set_values([])
            self.probability.set_points([])
            self.histogram.set_limits(None, None)
            self.trend.set_limits(None, None)
            self.probability.set_limits(None, None)
            return
        test_num = int(test_item.data(Qt.UserRole + 1))
        site_id = None if site_item is None else site_item.data(Qt.UserRole + 1)
        if site_id is not None:
            site_id = int(site_id)
        wafer_id = None if wafer_item is None else wafer_item.data(Qt.UserRole + 1)
        pin_id = None if pin_item is None else pin_item.data(Qt.UserRole + 1)
        if pin_id is not None:
            pin_id = str(pin_id)
        lo, hi = self.vm.limits_of(test_num, site_id, wafer_id, pin_id)
        self.histogram.set_limits(lo, hi)
        self.trend.set_limits(lo, hi)
        self.probability.set_limits(lo, hi)
        self.histogram.set_bins(self.vm.histogram(test_num, site=site_id, wafer=wafer_id, pin=pin_id))
        self.trend.set_values(self.vm.trend(test_num, site_id, wafer_id, pin_id))
        self.probability.set_points(self.vm.probability(test_num, site_id, wafer_id, pin_id))
