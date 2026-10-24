"""Handler simulator: trays, site layout, jam injection and sorting."""

from __future__ import annotations

import random
from typing import Iterator, Sequence

from ..config.models import HandlerConfig
from ..engine.context import TouchdownInfo

__all__ = ["HandlerSimulator"]


class HandlerSimulator:
    """Generates touchdowns for one tray of parts."""

    def __init__(self, parts: Sequence[str], config: HandlerConfig, sites: int,
                 tray_id: str = "T01", seed: int = 0) -> None:
        self.parts = list(parts)
        self.config = config
        self.sites = max(1, sites)
        self.tray_id = tray_id
        self.rng = random.Random(seed ^ 0xBEEF)

    def grid(self) -> tuple[int, int]:
        """Input-tray columns and rows. Rows grow when the lot exceeds the grid."""
        cols = max(1, int(self.config.tray_cols))
        rows = max(1, int(self.config.tray_rows))
        count = len(self.parts)
        if cols * rows < count:
            rows = (count + cols - 1) // cols
        return cols, rows

    def touchdowns(self) -> Iterator[TouchdownInfo]:
        """Pick ``sites`` slots at a time, left to right, top to bottom.

        The last pick masks the sites that have no part. ``x``/``y`` are the
        tray column and row, so a retest returns to the same slot.
        """
        cols, rows = self.grid()
        for touch_id, start in enumerate(range(0, len(self.parts), self.sites), start=1):
            chunk = self.parts[start:start + self.sites]
            entries = tuple(
                (offset + 1, (start + offset) % cols, (start + offset) // cols, part_id)
                for offset, part_id in enumerate(chunk)
            )
            jam = self.config.jam_prob > 0 and self.rng.random() < self.config.jam_prob
            prob = float(getattr(self.config, "contact_fail_prob", 0.0) or 0.0)
            contact_fail = {
                slot for slot, _x, _y, _pid in entries
                if slot > 1 and prob > 0 and self.rng.random() < prob
            }
            yield TouchdownInfo(
                touch_id=touch_id,
                unit_id=self.tray_id,
                sites=entries,
                index_time_ms=self.config.cycle_time_ms + self.config.index_time_ms,
                xtra={
                    "tray": self.tray_id,
                    "tray_cols": cols,
                    "tray_rows": rows,
                    "slot_start": start,
                    "jam": jam,
                    "jam_recovery_ms": self.config.jam_recovery_ms if jam else 0.0,
                    "contact_fail_sites": contact_fail,
                    "masked_sites": list(range(len(chunk) + 1, self.sites + 1)),
                },
            )

    @property
    def part_count(self) -> int:
        """Number of parts in the tray."""
        return len(self.parts)
