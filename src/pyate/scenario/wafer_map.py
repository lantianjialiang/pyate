"""Wafer geometry, die grid and bin map rendering."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..config.models import WaferConfig

__all__ = ["WaferDie", "WaferMap"]

_BIN_CHARS = ".123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class WaferDie:
    """One die on the wafer grid."""

    x: int
    y: int
    part_id: str

    @property
    def coord(self) -> tuple[int, int]:
        """Grid coordinate tuple."""
        return (self.x, self.y)


@dataclass
class WaferMap:
    """Circular die grid for a 300 mm style wafer."""

    config: WaferConfig
    wafer_id: str = "W01"
    dies: dict[tuple[int, int], WaferDie] = field(default_factory=dict)
    _bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

    def __post_init__(self) -> None:
        self.dies = self._generate()

    # -------------------------------------------------------------- geometry
    @property
    def radius_mm(self) -> float:
        """Wafer radius in millimetres."""
        return self.config.diameter_mm / 2.0

    @property
    def usable_radius_mm(self) -> float:
        """Radius after edge exclusion."""
        return self.radius_mm - self.config.edge_exclusion_mm

    def die_center_mm(self, x: int, y: int) -> tuple[float, float]:
        """Physical centre of die ``(x, y)`` in millimetres."""
        die_w, die_h = self.config.die_size_mm
        return (x * die_w, y * die_h)

    def _generate(self) -> dict[tuple[int, int], WaferDie]:
        die_w, die_h = self.config.die_size_mm
        radius = self.usable_radius_mm
        nx = int(radius / die_w) + 1
        ny = int(radius / die_h) + 1
        cx, cy = self.config.center_die
        skip = {(int(x), int(y)) for x, y in self.config.skip_list}
        dies: dict[tuple[int, int], WaferDie] = {}
        for iy in range(cy - ny, cy + ny + 1):
            for ix in range(cx - nx, cx + nx + 1):
                px, py = self.die_center_mm(ix, iy)
                if math.hypot(px, py) > radius:
                    continue
                if (ix, iy) in skip:
                    continue
                dies[(ix, iy)] = WaferDie(ix, iy, f"{self.wafer_id}-X{ix}Y{iy}")
        if dies:
            xs = [key[0] for key in dies]
            ys = [key[1] for key in dies]
            self._bounds = (min(xs), max(xs), min(ys), max(ys))
        return dies

    # ----------------------------------------------------------------- access
    @property
    def die_count(self) -> int:
        """Number of testable dies."""
        return len(self.dies)

    @property
    def area_estimate(self) -> float:
        """Analytic die count estimate (used by the acceptance test)."""
        die_w, die_h = self.config.die_size_mm
        return math.pi * self.usable_radius_mm ** 2 / (die_w * die_h)

    def part_id(self, x: int, y: int) -> str:
        """Part id of a die."""
        return self.dies[(x, y)].part_id

    def ordered(self, scan: str = "serpentine") -> list[tuple[int, int]]:
        """Die coordinates in a stable scan order."""
        if not self.dies:
            return []
        min_x, max_x, min_y, max_y = self._bounds
        coords: list[tuple[int, int]] = []
        for row, y in enumerate(range(min_y, max_y + 1)):
            xs = range(min_x, max_x + 1)
            if scan == "serpentine" and row % 2 == 1:
                xs = reversed(list(xs))
            for x in xs:
                if (x, y) in self.dies:
                    coords.append((x, y))
        return coords

    def grid_bounds(self) -> tuple[int, int, int, int]:
        """``(min_x, max_x, min_y, max_y)`` covered by the die grid."""
        return self._bounds

    def nearest_edge_distance(self, x: int, y: int) -> float:
        """Distance from the die centre to the wafer edge (millimetres)."""
        px, py = self.die_center_mm(x, y)
        return self.radius_mm - math.hypot(px, py)

    # ---------------------------------------------------------------- reports
    def bin_counts(self, bins: dict[tuple[int, int], int] | None = None) -> dict[int, int]:
        """Count dies per bin (``0`` = not tested)."""
        counts: dict[int, int] = {}
        for coord in self.dies:
            value = 0 if bins is None else int(bins.get(coord, 0))
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items()))

    def yield_of(self, bins: dict[tuple[int, int], int], pass_bin: int = 1) -> float:
        """Passing fraction in percent (untested dies count as failing)."""
        if not self.dies:
            return 0.0
        passed = sum(1 for coord in self.dies if bins.get(coord) == pass_bin)
        return 100.0 * passed / len(self.dies)

    def render_text(self, bins: dict[tuple[int, int], int] | None = None, *,
                    title: str | None = None) -> str:
        """ASCII bin map (row order: top of the wafer first)."""
        if not self.dies:
            return "(empty wafer map)"
        min_x, max_x, min_y, max_y = self._bounds
        header = title or f"Wafer {self.wafer_id}  dies={self.die_count}"
        lines = [header, "    " + "".join(str(abs(x) % 10) for x in range(min_x, max_x + 1))]
        for y in range(max_y, min_y - 1, -1):
            row = []
            for x in range(min_x, max_x + 1):
                if (x, y) not in self.dies:
                    row.append(" ")
                else:
                    value = 0 if bins is None else int(bins.get((x, y), 0))
                    row.append(_BIN_CHARS[value] if 0 <= value < len(_BIN_CHARS) else "?")
            lines.append(f"{y:4d}" + "".join(row))
        legend = "legend: ' '=no die  '.'=bin1 pass  '2'..'9'=fail bins"
        lines.append(legend)
        return "\n".join(lines)

    def render_svg(self, bins: dict[tuple[int, int], int] | None = None,
                   path: str | Path | None = None, *, cell: int = 12) -> str:
        """Render the bin map as a standalone SVG document."""
        min_x, max_x, min_y, max_y = self._bounds
        width = (max_x - min_x + 1) * cell + 20
        height = (max_y - min_y + 1) * cell + 40
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#0f1720"/>',
            f'<text x="10" y="20" fill="#e6edf3" font-family="monospace" font-size="13">'
            f'{self.wafer_id}  dies={self.die_count}</text>',
        ]
        palette = {0: "#2d333b", 1: "#1f6f43"}
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if (x, y) not in self.dies:
                    continue
                value = 0 if bins is None else int(bins.get((x, y), 0))
                colour = palette.get(value, "#b4453c")
                px = 10 + (x - min_x) * cell
                py = 30 + (max_y - y) * cell
                parts.append(
                    f'<rect x="{px}" y="{py}" width="{cell - 1}" height="{cell - 1}" '
                    f'fill="{colour}"><title>({x},{y}) bin {value}</title></rect>'
                )
        parts.append("</svg>")
        svg = "\n".join(parts)
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8")
        return svg

    def render_csv(self, bins: dict[tuple[int, int], int], path: str | Path) -> Path:
        """Write ``x,y,bin,part_id`` rows."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["x,y,bin,part_id"]
        for (x, y), die in sorted(self.dies.items()):
            lines.append(f"{x},{y},{int(bins.get((x, y), 0))},{die.part_id}")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def iter_dies(self) -> Iterable[WaferDie]:
        """Iterate every die."""
        return self.dies.values()
