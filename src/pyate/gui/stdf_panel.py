"""STDF file viewer. The test table and charts are the Run summary, plus file stats."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
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


class StdfPanel(QWidget):
    """Open an STDF file, show its lot statistics, and summarise the tests."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = RunViewModel()
        self._keys: list[tuple[int, int | None, str | None]] = []
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

        self.stats = QLabel("no STDF file")
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.stats)

        self.table = QTableWidget(0, len(SUMMARY_COLUMNS))
        self.table.setHorizontalHeaderLabels(SUMMARY_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        configure_table(self.table)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(4, 140)
        self.table.itemSelectionChanged.connect(self._paint)
        _mono(self.table)
        layout.addWidget(self.table, 2)

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
        layout.addLayout(charts, 1)

    def open_file(self) -> None:
        """Load an STDF file and rebuild the summary from its PTR/FTR records."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STDF", "", "STDF (*.stdf *.stdf.gz *.std *.gz)")
        if not path:
            return
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
                lo=row.get("lo"),
                hi=row.get("hi"),
            )
        self.stats.setText(_format_stats(report))
        self._keys = []
        self._fill()

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
                "" if row.part is None else row.part,
                row.count, row.passed, row.failed,
                row.mean, row.stdev, row.minimum, row.maximum,
                row.lo, row.hi, row.cp, row.cpk,
            )
            for column, text in enumerate(cells):
                _put_cell(self.table, index, column, text, sorts[column])
            self.table.item(index, 0).setData(Qt.UserRole + 1, row.test_num)
            self.table.item(index, 2).setData(Qt.UserRole + 1, row.site)
            self.table.item(index, 3).setData(Qt.UserRole + 1, row.wafer)
            self.table.item(index, 4).setData(Qt.UserRole + 1, row.part)
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
        part_item = self.table.item(row_index, 4) if row_index >= 0 else None
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
        part_id = None if part_item is None else part_item.data(Qt.UserRole + 1)
        lo, hi = self.vm.limits_of(test_num, site_id, wafer_id, part_id)
        self.histogram.set_limits(lo, hi)
        self.trend.set_limits(lo, hi)
        self.probability.set_limits(lo, hi)
        self.histogram.set_bins(self.vm.histogram(test_num, site=site_id, wafer=wafer_id, part=part_id))
        self.trend.set_values(self.vm.trend(test_num, site_id, wafer_id, part_id))
        self.probability.set_points(self.vm.probability(test_num, site_id, wafer_id, part_id))


def _format_stats(report: dict) -> str:
    """One block of file-level STDF statistics, above the per-test table."""
    if report.get("errors"):
        return f"{report.get('path', '')}\n" + "\n".join(str(item) for item in report["errors"])
    stats = report["stats"]
    wafers = ", ".join(
        f"{row['wafer_id'] or '(none)'} {row['good']}/{row['parts']}"
        for row in stats["wafers"]
    ) or "-"
    hard = ", ".join(
        f"{row['bin']} {row['name'] or ''} {row['count']}".strip()
        for row in stats["hard_bins"]
    ) or "-"
    soft = ", ".join(
        f"{row['bin']} {row['name'] or ''} {row['count']}".strip()
        for row in stats["soft_bins"]
    ) or "-"
    records = " ".join(f"{name}={count}" for name, count in stats["record_counts"].items())
    return (
        f"{report['path']}\n"
        f"lot {stats['lot_id'] or '-'}   part {stats['part_typ'] or '-'}   "
        f"job {stats['job_nam'] or '-'}   node {stats['node_nam'] or '-'}   "
        f"stage {stats['test_cod'] or '-'}   temp {stats['tst_temp'] or '-'}\n"
        f"parts {stats['parts']}   good {stats['good']}   "
        f"yield {stats['yield_pct']:.2f} %   retests {stats['retests']}   "
        f"PTR {stats['ptr_count']}   FTR {stats['ftr_count']}\n"
        f"wafers: {wafers}\n"
        f"hard bins: {hard}\n"
        f"soft bins: {soft}\n"
        f"records: {records}"
    )
