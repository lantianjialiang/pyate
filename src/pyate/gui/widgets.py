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
        painter.setPen(QPen(QColor(palette["outline"]), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(outline)
        if not self._dies:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(area, Qt.AlignCenter, "no data")
            return
        painter.setClipPath(outline)
        # die coordinates are grid indices, so the pitch is one die size
        scale = side / self._wafer_size
        cell = max(1.0, self._die_size * scale)
        colors = bins_colors()
        cx, cy = area.center().x(), area.center().y()
        hits: list[tuple[QRectF, int, int, int]] = []
        for (x, y), soft_bin in self._dies.items():
            px = cx + x * cell - cell / 2.0
            py = cy - y * cell - cell / 2.0
            rect = QRectF(px, py, cell - 0.5, cell - 0.5)
            painter.fillRect(rect, QColor(colors.get(soft_bin, "#7f8c8d")))
            hits.append((rect, int(x), int(y), int(soft_bin)))
        self._hits = hits
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


class HistogramWidget(QWidget):
    """Bar chart of ``(low, high, count)`` bins."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bins: list[tuple[float, float, int]] = []
        self.setMinimumHeight(110)

    def set_bins(self, bins: list[tuple[float, float, int]]) -> None:
        """Replace the histogram and repaint."""
        self._bins = list(bins)
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        painter.setPen(QColor(palette["muted"]))
        if not self._bins:
            painter.drawText(self.rect(), Qt.AlignCenter, "no histogram")
            return
        peak = max(count for _lo, _hi, count in self._bins) or 1
        left, top, right, bottom = 8, 8, 8, 18
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)
        span = width / len(self._bins)
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        for index, (low, _high, count) in enumerate(self._bins):
            bar = height * count / peak
            rect = QRectF(left + index * span + 1, top + height - bar, max(1.0, span - 2), bar)
            painter.fillRect(rect, QColor("#4b84f0"))
            if index in (0, len(self._bins) - 1):
                painter.setPen(QColor(palette["muted"]))
                painter.drawText(QRectF(rect.left() - 8, top + height, span + 16, 16),
                                 Qt.AlignCenter, f"{low:.3g}")


class TrendWidget(QWidget):
    """Polyline of the most recent measurements of one test."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(110)

    def set_values(self, values: list[float]) -> None:
        """Replace the series and repaint."""
        self._values = [float(value) for value in values]
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        if len(self._values) < 2:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no trend")
            return
        low = min(self._values)
        high = max(self._values)
        span = high - low or 1.0
        left, top, right, bottom = 8.0, 8.0, 8.0, 8.0
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)
        painter.setPen(QPen(QColor("#4b84f0"), 1.6))
        previous: QPointF | None = None
        for index, value in enumerate(self._values):
            x = left + width * index / (len(self._values) - 1)
            y = top + height * (1.0 - (value - low) / span)
            point = QPointF(x, y)
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point
        painter.setPen(QColor(palette["muted"]))
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        painter.drawText(QRectF(4, 2, self.width() - 8, 14), Qt.AlignRight,
                         f"{low:.3g} .. {high:.3g}")


class ProbabilityWidget(QWidget):
    """Normal probability plot: measurement on X, standard-normal z on Y."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []
        self.setMinimumHeight(110)

    def set_points(self, points: list[tuple[float, float]]) -> None:
        """Replace the ``(value, z)`` series and repaint."""
        self._points = [(float(x), float(z)) for x, z in points]
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
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
        low_x, high_x = min(xs), max(xs)
        low_z, high_z = min(zs), max(zs)
        span_x = high_x - low_x or 1.0
        span_z = high_z - low_z or 1.0
        left, top, right, bottom = 28.0, 8.0, 8.0, 16.0
        width = max(1.0, self.width() - left - right)
        height = max(1.0, self.height() - top - bottom)

        def px(value: float) -> float:
            return left + width * (value - low_x) / span_x

        def py(z: float) -> float:
            return top + height * (1.0 - (z - low_z) / span_z)

        mean = sum(xs) / len(xs)
        var = sum((value - mean) ** 2 for value in xs) / max(1, len(xs) - 1)
        stdev = math.sqrt(var) if var > 0 else 0.0
        painter.setPen(QPen(QColor(palette["muted"]), 1.0, Qt.DashLine))
        if stdev > 0:
            z0 = (low_x - mean) / stdev
            z1 = (high_x - mean) / stdev
            painter.drawLine(QPointF(px(low_x), py(z0)), QPointF(px(high_x), py(z1)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#4b84f0"))
        for value, z in self._points:
            painter.drawEllipse(QPointF(px(value), py(z)), 2.2, 2.2)
        painter.setPen(QColor(palette["muted"]))
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        painter.drawText(QRectF(4, self.height() - 14, self.width() - 8, 12), Qt.AlignLeft,
                         f"{low_x:.3g}")
        painter.drawText(QRectF(4, self.height() - 14, self.width() - 8, 12), Qt.AlignRight,
                         f"{high_x:.3g}")


class TrayWidget(QWidget):
    """Handler tray: one cell per site, coloured by the latest part result."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: list[dict[str, Any]] = []
        self._state: dict[int, dict[str, Any]] = {}
        self._masked: set[int] = set()
        self._jam = False
        self._title = ""
        self.setMinimumSize(220, 160)

    def set_data(self, touch: Mapping[str, Any], state: Mapping[int, Mapping[str, Any]]) -> None:
        """``touch`` is the latest TOUCHDOWN payload; ``state`` is per-site results."""
        self._slots = [dict(slot) for slot in touch.get("slots") or []]
        self._masked = {int(site) for site in touch.get("masked_sites") or []}
        self._jam = bool(touch.get("jam"))
        tray = touch.get("tray") or touch.get("unit") or ""
        touchdown = touch.get("touchdown") or ""
        self._title = f"{tray}  touchdown {touchdown}" + ("  JAM" if self._jam else "")
        self._state = {int(site): dict(info) for site, info in state.items()}
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        palette = canvas_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette["bg"]))
        painter.setPen(QColor(palette["text"]))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        if not self._slots and not self._title:
            painter.setPen(QColor(palette["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "no tray data")
            return
        painter.drawText(QRectF(8, 4, self.width() - 16, 16), Qt.AlignLeft, self._title or "tray")
        sites = self._slots or [{"site": site} for site in sorted(self._state)]
        count = max(1, len(sites))
        cols = max(1, int(count ** 0.5 + 0.999))
        rows = (count + cols - 1) // cols
        left, top = 8.0, 24.0
        cell_w = max(1.0, (self.width() - 16) / cols)
        cell_h = max(1.0, (self.height() - 32) / rows)
        colors = bins_colors()
        for index, slot in enumerate(sites):
            site = int(slot.get("site") or 0)
            info = self._state.get(site, {})
            col = index % cols
            row = index // cols
            rect = QRectF(left + col * cell_w + 2, top + row * cell_h + 2,
                          cell_w - 4, cell_h - 4)
            if site in self._masked and site not in self._state:
                colour = QColor(palette["outline"])
            else:
                colour = QColor(colors.get(int(info.get("bin") or 0), "#7f8c8d"))
            painter.fillRect(rect, colour)
            painter.setPen(QColor("#ffffff"))
            part = str(slot.get("part_id") or info.get("part_id") or "")
            painter.drawText(rect, Qt.AlignCenter, f"S{site}\n{part[:10]}")
            painter.setPen(QColor(palette["text"]))


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

