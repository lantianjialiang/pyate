"""Small custom widgets: wafer map, bin bars, shmoo grid."""

from __future__ import annotations

import math
from typing import Any, Mapping

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ..api import normalize_shmoo, shmoo_csv
from .theme import bins_colors, canvas_colors

__all__ = [
    "WaferMapWidget", "BinBarWidget", "ShmooWidget",
    "HistogramWidget", "TrendWidget", "ProbabilityWidget", "TrayWidget", "PatternCompareWidget",
]


class WaferMapWidget(QWidget):
    """Draws a CP wafer map (colour = soft bin) with a circular wafer outline."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dies: dict[tuple[int, int], int] = {}
        self._details: dict[tuple[int, int], dict[str, Any]] = {}
        self._hits: list[tuple[QRectF, int, int, int]] = []
        self._wafer_size: float = 300.0
        self._die_size: float = 10.0
        self.setMinimumSize(260, 260)
        self.setMouseTracking(True)
        self.setToolTip("wafer map")

    def set_data(self, dies: Mapping[tuple[int, int], int], *,
                 wafer_size_mm: float = 300.0, die_size_mm: float = 10.0,
                 details: Mapping[tuple[int, int], Mapping[str, Any]] | None = None) -> None:
        """Replace the die data and repaint."""
        self._dies = {(int(x), int(y)): int(b) for (x, y), b in dies.items()}
        self._wafer_size = float(wafer_size_mm or 300.0)
        self._die_size = float(die_size_mm or 10.0)
        if details is not None:
            self._details = {(int(x), int(y)): dict(info) for (x, y), info in details.items()}
        self.update()

    def set_details(self, details: Mapping[tuple[int, int], Mapping[str, Any]]) -> None:
        """Per-die hover text (bin, first failing test)."""
        self._details = {(int(x), int(y)): dict(info) for (x, y), info in details.items()}
        self.update()

    def export_png(self, path: str) -> bool:
        """Render the current map into a PNG file."""
        return bool(self.grab().save(path))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(6, 6, -6, -6)
        side = min(rect.width(), rect.height())
        # Sit the circle on the bottom edge. Extra widget height stays above
        # the map, so the band under the wafer is only the bin legend.
        area = QRectF(rect.center().x() - side / 2.0, rect.bottom() - side,
                      float(side), float(side))
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        outline = self._outline(area)
        if not self._dies:
            painter.setPen(QPen(QColor(palette["outline"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(outline)
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(area, Qt.AlignCenter, "no data")
            return
        # Each die stays a full rectangle. The circle is only an outline,
        # drawn after the dice so a die on the rim is not cut into a polygon.
        scale = side / self._wafer_size
        cell = max(1.0, self._die_size * scale)
        colors = bins_colors()
        cx, cy = area.center().x(), area.center().y()
        hits: list[tuple[QRectF, int, int, int]] = []
        for (x, y), soft_bin in self._dies.items():
            px = cx + x * cell - cell / 2.0
            py = cy - y * cell - cell / 2.0
            die = QRectF(px, py, cell - 0.5, cell - 0.5)
            painter.fillRect(die, QColor(colors.get(soft_bin, "#7f8c8d")))
            hits.append((die, int(x), int(y), int(soft_bin)))
        self._hits = hits
        painter.setPen(QPen(QColor(palette["outline"]), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(outline)
        painter.setPen(QColor(palette["muted"]))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(area.adjusted(4, 4, -4, -4), Qt.AlignTop | Qt.AlignLeft,
                         f"{len(self._dies)} dies")

    @staticmethod
    def _outline(area: QRectF) -> QPainterPath:
        """Circle with a small bottom alignment notch.

        The cut is a few percent of the radius (a 300 mm wafer notch is about
        1 mm). It is not a flat: a flat would take a large chord out of the map.
        """
        path = QPainterPath()
        path.addEllipse(area)
        radius = area.width() / 2.0
        depth = max(4.0, radius * 0.045)
        half = depth * 1.15
        cx = area.center().x()
        bottom = area.bottom()
        notch = QPainterPath()
        notch.moveTo(cx - half, bottom + 1.0)
        notch.lineTo(cx, bottom - depth)
        notch.lineTo(cx + half, bottom + 1.0)
        notch.closeSubpath()
        return path.subtracted(notch)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        point = event.position()
        for rect, x, y, soft_bin in self._hits:
            if rect.contains(point):
                info = self._details.get((x, y), {})
                lines = [f"x {x}  y {y}", f"bin {info.get('bin', soft_bin)}"]
                fail_name = info.get("fail_name") or ""
                if fail_name:
                    lines.append(f"fail {fail_name} = {info.get('fail_value')}")
                elif info.get("passed"):
                    lines.append("pass")
                QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        QToolTip.hideText()
        super().leaveEvent(event)


class BinBarWidget(QWidget):
    """Horizontal stacked bar of the soft bin distribution."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[int, int, float]] = []
        self.setMinimumHeight(64)
        self.setMaximumHeight(72)

    def set_data(self, rows: list[tuple[int, int, float]]) -> None:
        """``rows`` are ``(bin, count, pct)`` triples."""
        self._rows = list(rows)
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if not self._rows:
            return
        colors = bins_colors()
        total = sum(count for _b, count, _p in self._rows) or 1
        width = self.width() - 12
        x = 6.0
        top = 10.0
        height = 26.0
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        for soft_bin, count, _pct in self._rows:
            span = width * count / total
            painter.fillRect(QRectF(x, top, span, height),
                             QColor(colors.get(soft_bin, "#7f8c8d")))
            if span > 28:
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(x, top, span, height), Qt.AlignCenter,
                                 f"{soft_bin}")
            x += span
        painter.setPen(QColor(palette["text"]))
        text = "  ".join(f"bin {b}: {c} ({p:.1f}%)" for b, c, p in self._rows[:6])
        painter.drawText(QRectF(6, top + height + 6, width, 24), Qt.AlignLeft, text)


class ShmooWidget(QWidget):
    """2D colour map (rows = VDD, columns = period) for shmoo plots."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._x: list[float] = []
        self._y: list[float] = []
        self._pass: list[list[bool]] = []
        self._rate = 0.0
        self._x_label = "period_ns"
        self._y_label = "VDD"
        self._plot = QRectF()
        self.setMinimumSize(280, 220)
        self.setMouseTracking(True)

    @property
    def has_data(self) -> bool:
        """True when a matrix has been loaded."""
        return bool(self._pass)

    def set_data(self, data: Mapping[str, Any]) -> None:
        """Accept a raw shmoo payload or the normalized ``x/y/pass`` shape."""
        normal = normalize_shmoo(data)
        self._x = list(normal["x"])
        self._y = list(normal["y"])
        self._pass = [list(row) for row in normal["pass"]]
        self._rate = float(normal["pass_rate"])
        self._x_label = str(normal["x_label"])
        self._y_label = str(normal["y_label"])
        self.update()

    def export_png(self, path: str) -> bool:
        """Render the current plot into a PNG file."""
        return bool(self.grab().save(path))

    def export_csv(self, path: str) -> bool:
        """Write the pass/fail matrix as CSV."""
        if not self._pass:
            return False
        from pathlib import Path

        Path(path).write_text(shmoo_csv({
            "x": self._x, "y": self._y, "pass": self._pass,
            "pass_rate": self._rate, "x_label": self._x_label, "y_label": self._y_label,
        }), encoding="utf-8")
        return True

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if not self._pass:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no shmoo data")
            self._plot = QRectF()
            return
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        margin_left, margin_bottom, margin_top, margin_right = 52, 36, 22, 12
        plot = QRectF(margin_left, margin_top,
                      max(1.0, self.width() - margin_left - margin_right),
                      max(1.0, self.height() - margin_top - margin_bottom))
        self._plot = plot
        rows = len(self._pass)
        cols = max(len(row) for row in self._pass)
        cell_w = plot.width() / max(1, cols)
        cell_h = plot.height() / max(1, rows)
        for row_index, row in enumerate(self._pass):
            for col_index, ok in enumerate(row):
                painter.fillRect(
                    QRectF(plot.left() + col_index * cell_w,
                           plot.top() + (rows - 1 - row_index) * cell_h,
                           max(1.0, cell_w - 1.0), max(1.0, cell_h - 1.0)),
                    QColor("#2e9e5b" if ok else "#c0392b"),
                )
        painter.setPen(QColor(palette["text"]))
        self._tick(painter, plot, self._x, horizontal=True)
        self._tick(painter, plot, self._y, horizontal=False)
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(8, self.height() - 18, self.width() - 16, 16),
                         Qt.AlignLeft, self._x_label)
        painter.save()
        painter.translate(12, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-40, -10, 80, 16), Qt.AlignCenter, self._y_label)
        painter.restore()
        painter.setPen(QColor(palette["text"]))
        painter.fillRect(QRectF(self.width() - 118, 4, 10, 10), QColor("#2e9e5b"))
        painter.drawText(QRectF(self.width() - 104, 2, 36, 14), Qt.AlignLeft, "pass")
        painter.fillRect(QRectF(self.width() - 66, 4, 10, 10), QColor("#c0392b"))
        painter.drawText(QRectF(self.width() - 52, 2, 48, 14), Qt.AlignLeft,
                         f"fail  {self._rate:.0f}%")

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        cell = self._cell_at(event.position())
        if cell is None:
            QToolTip.hideText()
            return
        x_value, y_value, ok = cell
        verdict = "PASS" if ok else "FAIL"
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{self._y_label} {y_value:.4g}\n{self._x_label} {x_value:.4g}\n{verdict}",
            self,
        )

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        QToolTip.hideText()
        super().leaveEvent(event)

    def _cell_at(self, point: Any) -> tuple[float, float, bool] | None:
        if not self._pass or not self._plot.contains(point):
            return None
        rows = len(self._pass)
        cols = max(len(row) for row in self._pass)
        cell_w = self._plot.width() / max(1, cols)
        cell_h = self._plot.height() / max(1, rows)
        col = int((point.x() - self._plot.left()) / cell_w)
        row_from_top = int((point.y() - self._plot.top()) / cell_h)
        row = rows - 1 - row_from_top
        if row < 0 or row >= rows or col < 0 or col >= len(self._pass[row]):
            return None
        x_value = self._x[col] if col < len(self._x) else float(col)
        y_value = self._y[row] if row < len(self._y) else float(row)
        return x_value, y_value, bool(self._pass[row][col])

    def _tick(self, painter: QPainter, plot: QRectF, values: list[float], *,
              horizontal: bool) -> None:
        if not values:
            return
        indexes = [0, len(values) // 2, len(values) - 1]
        seen: set[int] = set()
        for index in indexes:
            if index in seen:
                continue
            seen.add(index)
            label = f"{values[index]:.4g}"
            if horizontal:
                x = plot.left() + (index + 0.5) * plot.width() / max(1, len(self._pass[0]))
                painter.drawText(QRectF(x - 28, plot.bottom() + 2, 56, 14), Qt.AlignCenter, label)
            else:
                y = plot.bottom() - (index + 0.5) * plot.height() / max(1, len(self._pass))
                painter.drawText(QRectF(0, y - 8, plot.left() - 4, 16),
                                 Qt.AlignRight | Qt.AlignVCenter, label)


_LO_COLOR = "#d4880f"
_HI_COLOR = "#c0392b"


def _chart_span(low: float, high: float,
                lo: float | None, hi: float | None) -> tuple[float, float]:
    """Axis range that still shows the data and any spec limits."""
    values = [low, high]
    if lo is not None:
        values.append(lo)
    if hi is not None:
        values.append(hi)
    start, end = min(values), max(values)
    if start == end:
        pad = abs(start) * 0.08 or 1.0
        return start - pad, end + pad
    pad = (end - start) * 0.06
    return start - pad, end + pad


def _limit_note(lo: float | None, hi: float | None) -> str:
    """One line naming the spec limits, for a tooltip."""
    parts: list[str] = []
    if lo is not None:
        parts.append(f"lo {lo:.4g}")
    if hi is not None:
        parts.append(f"hi {hi:.4g}")
    return "   ".join(parts)


class _StatChart(QWidget):
    """Shared lower/high limit lines and hover text for the summary charts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo: float | None = None
        self._hi: float | None = None
        self._lo_pos: float | None = None
        self._hi_pos: float | None = None
        self._hits: list[tuple[Any, ...]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(110)

    def set_limits(self, lo: float | None, hi: float | None) -> None:
        """Draw these spec limits. ``None`` means that side is unbounded."""
        self._lo = None if lo is None else float(lo)
        self._hi = None if hi is None else float(hi)
        self.update()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        QToolTip.hideText()
        super().leaveEvent(event)

    def _clear_hits(self) -> None:
        self._lo_pos = None
        self._hi_pos = None
        self._hits = []

    def _tip(self, event: Any, text: str) -> None:
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def _limit_hit(self, x: float, y: float, *, vertical: bool) -> str:
        if vertical:
            if self._lo is not None and self._lo_pos is not None and abs(x - self._lo_pos) <= 6:
                return f"lower limit\n{self._lo:.6g}"
            if self._hi is not None and self._hi_pos is not None and abs(x - self._hi_pos) <= 6:
                return f"high limit\n{self._hi:.6g}"
            return ""
        if self._lo is not None and self._lo_pos is not None and abs(y - self._lo_pos) <= 6:
            return f"lower limit\n{self._lo:.6g}"
        if self._hi is not None and self._hi_pos is not None and abs(y - self._hi_pos) <= 6:
            return f"high limit\n{self._hi:.6g}"
        return ""

    def _draw_vlimit(self, painter: QPainter, x: float, top: float, bottom: float,
                     color: str, label: str) -> None:
        painter.setPen(QPen(QColor(color), 1.2, Qt.DashLine))
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.setPen(QColor(color))
        painter.drawText(QRectF(x - 22, top, 44, 12), Qt.AlignHCenter | Qt.AlignTop, label)

    def _draw_hlimit(self, painter: QPainter, y: float, left: float, right: float,
                     color: str, label: str) -> None:
        painter.setPen(QPen(QColor(color), 1.2, Qt.DashLine))
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(QColor(color))
        painter.drawText(QRectF(left + 2, y - 12, 36, 12), Qt.AlignLeft | Qt.AlignVCenter, label)

    def _paint_limits(self, painter: QPainter, *, vertical: bool,
                      place: Any, top: float, bottom: float, left: float, right: float) -> None:
        if self._lo is not None:
            pos = place(self._lo)
            self._lo_pos = pos
            if vertical:
                self._draw_vlimit(painter, pos, top, bottom, _LO_COLOR, "L")
            else:
                self._draw_hlimit(painter, pos, left, right, _LO_COLOR, "L")
        if self._hi is not None:
            pos = place(self._hi)
            self._hi_pos = pos
            if vertical:
                self._draw_vlimit(painter, pos, top, bottom, _HI_COLOR, "H")
            else:
                self._draw_hlimit(painter, pos, left, right, _HI_COLOR, "H")


class HistogramWidget(_StatChart):
    """Bar chart of ``(low, high, count)`` bins, with optional spec limits."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bins: list[tuple[float, float, int]] = []

    def set_bins(self, bins: list[tuple[float, float, int]]) -> None:
        """Replace the histogram and repaint."""
        self._bins = list(bins)
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._clear_hits()
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        painter.setPen(QColor(palette["muted"]))
        if not self._bins:
            painter.drawText(self.rect(), Qt.AlignCenter, "no histogram")
            return
        peak = max(count for _lo, _hi, count in self._bins) or 1
        data_low = min(low for low, _high, _count in self._bins)
        data_high = max(high for _low, high, _count in self._bins)
        axis_low, axis_high = _chart_span(data_low, data_high, self._lo, self._hi)
        left, top, right, bottom = 8.0, 16.0, 8.0, 18.0
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)
        span = axis_high - axis_low or 1.0

        def x_of(value: float) -> float:
            return left + width * (value - axis_low) / span

        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        for low, high, count in self._bins:
            x0 = x_of(low)
            x1 = max(x0 + 1.0, x_of(high))
            bar = height * count / peak
            rect = QRectF(x0 + 0.5, top + height - bar, max(1.0, x1 - x0 - 1.0), bar)
            painter.fillRect(rect, QColor("#4b84f0"))
            self._hits.append((x0, x1, low, high, count))
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(left, top + height, 64, 16), Qt.AlignLeft, f"{data_low:.3g}")
        painter.drawText(QRectF(left + width - 64, top + height, 64, 16), Qt.AlignRight,
                         f"{data_high:.3g}")
        self._paint_limits(painter, vertical=True, place=x_of,
                           top=top, bottom=top + height, left=left, right=left + width)
        note = _limit_note(self._lo, self._hi)
        if note:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(QRectF(left, 1, width, 14), Qt.AlignRight, note)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        x = event.position().x()
        text = self._limit_hit(x, event.position().y(), vertical=True)
        if not text:
            for left, right, low, high, count in self._hits:
                if left <= x <= right:
                    text = f"{low:.4g} .. {high:.4g}\ncount {count}"
                    note = _limit_note(self._lo, self._hi)
                    if note:
                        text = f"{text}\n{note}"
                    break
        self._tip(event, text)


class TrendWidget(_StatChart):
    """Polyline of the most recent measurements of one test."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []

    def set_values(self, values: list[float]) -> None:
        """Replace the series and repaint."""
        self._values = [float(value) for value in values]
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._clear_hits()
        palette = canvas_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if len(self._values) < 2:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no trend")
            return
        axis_low, axis_high = _chart_span(min(self._values), max(self._values), self._lo, self._hi)
        span = axis_high - axis_low or 1.0
        left, top, right, bottom = 8.0, 16.0, 8.0, 8.0
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)

        def y_of(value: float) -> float:
            return top + height * (1.0 - (value - axis_low) / span)

        painter.setPen(QPen(QColor("#4b84f0"), 1.6))
        previous: QPointF | None = None
        for index, value in enumerate(self._values):
            x = left + width * index / (len(self._values) - 1)
            point = QPointF(x, y_of(value))
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point
            self._hits.append((x, point.y(), index, value))
        self._paint_limits(painter, vertical=False, place=y_of,
                           top=top, bottom=top + height, left=left, right=left + width)
        painter.setPen(QColor(palette["muted"]))
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        note = _limit_note(self._lo, self._hi)
        caption = f"{min(self._values):.3g} .. {max(self._values):.3g}"
        if note:
            caption = f"{caption}    {note}"
        painter.drawText(QRectF(left, 1, width, 14), Qt.AlignRight, caption)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        x = event.position().x()
        y = event.position().y()
        text = self._limit_hit(x, y, vertical=False)
        if not text and self._hits:
            nearest: tuple[int, float] | None = None
            best = 14.0
            for px, py, index, value in self._hits:
                dist = math.hypot(px - x, py - y)
                if dist <= best:
                    best = dist
                    nearest = (index, value)
            if nearest is not None:
                text = f"#{nearest[0] + 1}\nvalue {nearest[1]:.6g}"
                note = _limit_note(self._lo, self._hi)
                if note:
                    text = f"{text}\n{note}"
        self._tip(event, text)


class ProbabilityWidget(_StatChart):
    """Normal probability plot: measurement on X, standard-normal z on Y."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []

    def set_points(self, points: list[tuple[float, float]]) -> None:
        """Replace the ``(value, z)`` series and repaint."""
        self._points = [(float(x), float(z)) for x, z in points]
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._clear_hits()
        palette = canvas_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if len(self._points) < 2:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no probability")
            return
        xs = [point[0] for point in self._points]
        zs = [point[1] for point in self._points]
        data_low, data_high = min(xs), max(xs)
        axis_low, axis_high = _chart_span(data_low, data_high, self._lo, self._hi)
        low_z, high_z = min(zs), max(zs)
        span_x = axis_high - axis_low or 1.0
        span_z = high_z - low_z or 1.0
        left, top, right, bottom = 28.0, 16.0, 8.0, 16.0
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)

        def px(value: float) -> float:
            return left + width * (value - axis_low) / span_x

        def py(z: float) -> float:
            return top + height * (1.0 - (z - low_z) / span_z)

        mean = sum(xs) / len(xs)
        var = sum((value - mean) ** 2 for value in xs) / max(1, len(xs) - 1)
        stdev = math.sqrt(var) if var > 0 else 0.0
        painter.setPen(QPen(QColor(palette["muted"]), 1.0, Qt.DashLine))
        if stdev > 0:
            z0 = (data_low - mean) / stdev
            z1 = (data_high - mean) / stdev
            painter.drawLine(QPointF(px(data_low), py(z0)), QPointF(px(data_high), py(z1)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#4b84f0"))
        for value, z in self._points:
            point = QPointF(px(value), py(z))
            painter.drawEllipse(point, 2.2, 2.2)
            self._hits.append((point.x(), point.y(), value, z))
        self._paint_limits(painter, vertical=True, place=px,
                           top=top, bottom=top + height, left=left, right=left + width)
        painter.setPen(QColor(palette["muted"]))
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        painter.drawText(QRectF(4, self.height() - 14, self.width() - 8, 12), Qt.AlignLeft,
                         f"{data_low:.3g}")
        painter.drawText(QRectF(4, self.height() - 14, self.width() - 8, 12), Qt.AlignRight,
                         f"{data_high:.3g}")
        note = _limit_note(self._lo, self._hi)
        if note:
            painter.drawText(QRectF(left, 1, width, 14), Qt.AlignRight, note)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        x = event.position().x()
        y = event.position().y()
        text = self._limit_hit(x, y, vertical=True)
        if not text and self._hits:
            nearest: tuple[float, float] | None = None
            best = 14.0
            for px, py, value, z in self._hits:
                dist = math.hypot(px - x, py - y)
                if dist <= best:
                    best = dist
                    nearest = (value, z)
            if nearest is not None:
                text = f"value {nearest[0]:.6g}\nz {nearest[1]:.3f}"
                note = _limit_note(self._lo, self._hi)
                if note:
                    text = f"{text}\n{note}"
        self._tip(event, text)


_SLOT_COLOR = {
    "untested": "#c5ced6",
    "testing": "#e6a817",
    "picked": "#8aa0b2",
}
_SITE_COLOR = {
    "idle": "#dfe4ea",
    "testing": "#e6a817",
    "pass": "#2e9e5b",
    "fail": "#c0392b",
    "masked": "#95a5a6",
}


class TrayWidget(QWidget):
    """FT handler view: input tray, the sockets, and the output bin pockets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict[str, Any] = {}
        self._hits: list[tuple[QRectF, str]] = []
        self.setMinimumSize(280, 240)
        self.setMouseTracking(True)

    def set_data(self, view: Mapping[str, Any], state: Mapping[int, Mapping[str, Any]] | None = None) -> None:
        """Replace the painted snapshot. ``state`` is ignored; the view carries site status."""
        self._view = dict(view or {})
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        self._hits = []
        cols = int(self._view.get("cols") or 0)
        rows = int(self._view.get("rows") or 0)
        if cols <= 0 or rows <= 0:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no tray data")
            return
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(palette["text"]))
        title = str(self._view.get("title") or "tray")
        tested = int(self._view.get("tested") or 0)
        total = int(self._view.get("total") or 0)
        jams = int(self._view.get("jams") or 0)
        jam = "  JAM" if self._view.get("jam_now") else ""
        painter.drawText(QRectF(8, 2, self.width() - 16, 16), Qt.AlignLeft,
                         f"{title}   {tested}/{total}{jam}   jam {jams}")
        self._legend(painter, palette, 20)
        grid_top = 40.0
        grid_bottom = self.height() - 78.0
        self._grid(painter, palette, cols, rows, grid_top, max(grid_top + 20, grid_bottom))
        self._sites(painter, palette, self.height() - 72.0)
        self._output(painter, palette, self.height() - 40.0)

    def _legend(self, painter: QPainter, palette: dict[str, str], top: float) -> None:
        painter.setPen(QColor(palette["text"]))
        x = 8.0
        for name, colour in (("未测", _SLOT_COLOR["untested"]),
                             ("测试中", _SLOT_COLOR["testing"]),
                             ("已取走", _SLOT_COLOR["picked"])):
            painter.fillRect(QRectF(x, top, 12, 12), QColor(colour))
            painter.drawText(QRectF(x + 16, top - 2, 52, 16), Qt.AlignLeft, name)
            x += 72

    def _grid(self, painter: QPainter, palette: dict[str, str], cols: int, rows: int,
              top: float, bottom: float) -> None:
        cells = {
            (int(slot.get("x") or 0), int(slot.get("y") or 0)): slot
            for slot in self._view.get("slots") or []
        }
        left = 8.0
        width = max(1.0, self.width() - 16.0)
        height = max(1.0, bottom - top)
        cell_w = width / cols
        cell_h = height / rows
        for row in range(rows):
            for col in range(cols):
                rect = QRectF(left + col * cell_w + 0.5, top + row * cell_h + 0.5,
                              max(1.0, cell_w - 1.0), max(1.0, cell_h - 1.0))
                slot = cells.get((col, row))
                if slot is None:
                    painter.fillRect(rect, QColor(palette["bg"]))
                    painter.setPen(QColor(palette["outline"]))
                    painter.drawRect(rect)
                    continue
                state = str(slot.get("state") or "untested")
                painter.fillRect(rect, QColor(_SLOT_COLOR.get(state, _SLOT_COLOR["untested"])))
                part = str(slot.get("part_id") or "")
                self._hits.append((rect, f"{part or 'empty'}\nrow {row} col {col}\n{state}"))

    def _sites(self, painter: QPainter, palette: dict[str, str], top: float) -> None:
        sites = list(self._view.get("sites") or [])
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(8, top, 36, 26), Qt.AlignVCenter, "site")
        if not sites:
            return
        left = 44.0
        width = max(1.0, (self.width() - left - 8) / len(sites))
        for index, site in enumerate(sites):
            status = str(site.get("status") or "idle")
            rect = QRectF(left + index * width + 1, top + 2, max(1.0, width - 2), 22)
            painter.fillRect(rect, QColor(_SITE_COLOR.get(status, _SITE_COLOR["idle"])))
            painter.setPen(QColor("#ffffff" if status in ("pass", "fail", "testing", "masked") else palette["text"]))
            painter.drawText(rect, Qt.AlignCenter, f"S{site.get('site')}\n{status}")
            painter.setPen(QColor(palette["muted"]))

    def _output(self, painter: QPainter, palette: dict[str, str], top: float) -> None:
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(8, top, 48, 28), Qt.AlignVCenter, "output")
        pockets = list(self._view.get("output") or [])
        colors = bins_colors()
        left = 58.0
        if not pockets:
            painter.drawText(QRectF(left, top, 160, 28), Qt.AlignVCenter, "no sorted parts")
            return
        width = max(28.0, min(64.0, (self.width() - left - 8) / len(pockets)))
        for index, pocket in enumerate(pockets):
            hard = int(pocket.get("bin") or 0)
            rect = QRectF(left + index * (width + 4), top + 2, width, 24)
            painter.fillRect(rect, QColor(colors.get(hard, "#7f8c8d")))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignCenter, f"H{hard} {pocket.get('count')}")

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        point = event.position()
        text = ""
        for rect, label in self._hits:
            if rect.contains(point):
                text = label
                break
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()


class PatternCompareWidget(QWidget):
    """Logic compare of one failing cycle: expected versus actual, per pin."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expected: dict[str, Any] = {}
        self._actual: dict[str, Any] = {}
        self._pins: list[str] = []
        self._fail_pins: set[str] = set()
        self.setMinimumHeight(120)

    def set_fail(self, expected: Mapping[str, Any] | None,
                 actual: Mapping[str, Any] | None,
                 fail_pins: list[str] | tuple[str, ...] | None = None) -> None:
        """Show one cycle. Empty input clears the strip."""
        self._expected = dict(expected or {})
        self._actual = dict(actual or {})
        pins = list(self._expected) or list(self._actual)
        for pin in self._actual:
            if pin not in pins:
                pins.append(pin)
        self._pins = pins
        self._fail_pins = {str(pin) for pin in (fail_pins or [])}
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if not self._pins:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no failing cycle")
            return
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        row_h = max(16.0, (self.height() - 28) / max(1, len(self._pins)))
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(QRectF(72, 2, 48, 14), Qt.AlignCenter, "exp")
        painter.drawText(QRectF(124, 2, 48, 14), Qt.AlignCenter, "act")
        for index, pin in enumerate(self._pins):
            top = 18 + index * row_h
            failed = pin in self._fail_pins or self._expected.get(pin) != self._actual.get(pin)
            painter.setPen(QColor("#c0392b" if failed else palette["text"]))
            painter.drawText(QRectF(6, top, 62, row_h), Qt.AlignVCenter, pin)
            self._bit(painter, QRectF(72, top + 2, 44, row_h - 4), self._expected.get(pin), failed)
            self._bit(painter, QRectF(124, top + 2, 44, row_h - 4), self._actual.get(pin), failed)

    @staticmethod
    def _bit(painter: QPainter, rect: QRectF, value: Any, failed: bool) -> None:
        painter.fillRect(rect, QColor("#c0392b" if failed else "#2e9e5b"))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignCenter, "-" if value is None else str(value))

