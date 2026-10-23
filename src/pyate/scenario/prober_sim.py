"""Prober simulator: turns a wafer map into touchdowns for a site layout."""

from __future__ import annotations

import random
from typing import Iterator

from ..config.models import ProberConfig
from ..engine.context import TouchdownInfo
from .wafer_map import WaferMap

__all__ = ["ProberSimulator"]


class ProberSimulator:
    """Generates touchdowns covering every die exactly once."""

    def __init__(self, wafer: WaferMap, config: ProberConfig, sites: int,
                 seed: int = 0) -> None:
        self.wafer = wafer
        self.config = config
        self.sites = max(1, sites)
        self.rng = random.Random(seed ^ 0xC0FFEE)

    def touchdowns(self) -> Iterator[TouchdownInfo]:
        """Yield touchdowns in a snake (boustrophedon) scan order.

        The touchdown footprint comes from the probe card layout, but when fewer
        sites are enabled than the card can hold the footprint shrinks with them.
        That keeps the die coverage complete: lowering ``run.sites`` changes the
        number of touchdowns, never the number of tested dies.
        """
        cols, rows = self._footprint()
        capacity = min(self.sites, cols * rows)
        min_x, max_x, min_y, max_y = self.wafer.grid_bounds()
        touch_id = 0
        for band_index, band_y in enumerate(range(min_y, max_y + 1, rows)):
            starts = list(range(min_x, max_x + 1, cols))
            if self.config.scan == "serpentine" and band_index % 2 == 1:
                starts.reverse()
            for start_x in starts:
                entries: list[tuple[int, int, int, str]] = []
                slot = 0
                for row in range(rows):
                    if slot >= capacity:
                        break
                    for col in range(cols):
                        if slot >= capacity:
                            break
                        x = start_x + col
                        y = band_y + row
                        slot += 1
                        die = self.wafer.dies.get((x, y))
                        if die is None:
                            continue
                        entries.append((slot, x, y, die.part_id))
                if not entries:
                    continue
                contact_fail = {
                    site for site, _x, _y, _pid in entries
                    if self.config.contact_fail_prob > 0
                    and self.rng.random() < self.config.contact_fail_prob
                }
                touch_id += 1
                yield TouchdownInfo(
                    touch_id=touch_id,
                    unit_id=self.wafer.wafer_id,
                    sites=tuple(entries),
                    index_time_ms=self.config.index_time_ms,
                    xtra={
                        "contact_fail_sites": contact_fail,
                        "layout": (cols, rows),
                        "masked_sites": [site for site in range(1, self.sites + 1)
                                         if site not in {entry[0] for entry in entries}],
                    },
                )

    def _footprint(self) -> tuple[int, int]:
        """Effective ``(cols, rows)`` footprint for the enabled site count."""
        cols = max(1, self.config.layout.cols)
        rows = max(1, self.config.layout.rows)
        if self.sites >= cols * rows:
            return cols, rows
        rows_eff = max(1, min(rows, self.sites))
        cols_eff = max(1, min(cols, -(-self.sites // rows_eff)))
        return cols_eff, rows_eff

    def coverage(self) -> dict[tuple[int, int], int]:
        """Count how often every die is touched (should be exactly once)."""
        counts: dict[tuple[int, int], int] = {}
        for touch in self.touchdowns():
            for _site, x, y, _pid in touch.sites:
                counts[(x, y)] = counts.get((x, y), 0) + 1
        return counts
