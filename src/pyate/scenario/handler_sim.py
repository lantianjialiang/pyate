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

    def touchdowns(self) -> Iterator[TouchdownInfo]:
        """Chunk the tray into touchdowns; the tail touchdown masks spare sites."""
        for touch_id, start in enumerate(range(0, len(self.parts), self.sites), start=1):
            chunk = self.parts[start:start + self.sites]
            entries = tuple(
                (slot, 0, 0, part_id) for slot, part_id in enumerate(chunk, start=1)
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
