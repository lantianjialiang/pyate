"""FT (final test) scenario using a handler."""

from __future__ import annotations

from typing import Iterator, Sequence

from .base import UnitPlan
from .handler_sim import HandlerSimulator

__all__ = ["FtScenario"]


class FtScenario:
    """One lot of trays/reels tested on a handler."""

    name = "FT"

    def __init__(self, session: object, sites: int, *, part_ids: Sequence[str] | None = None,
                 trays: int | None = None, parts_per_tray: int | None = None,
                 seed: int | None = None) -> None:
        self.session = session
        self.sites = sites
        self.seed = seed if seed is not None else int(getattr(session.run, "seed", 0))
        lot = getattr(session, "lot", None)
        self.handler = getattr(session, "handler", None)
        if self.handler is None:
            raise ValueError("FT scenario requires handler.yaml")
        self.trays = int(trays if trays is not None else (lot.trays if lot else 1) or 1)
        self.parts_per_tray = int(
            parts_per_tray if parts_per_tray is not None
            else (lot.parts_per_tray if lot else self.handler.tray_capacity) or 500
        )
        self.lot_id = getattr(lot, "lot_id", "LOT") if lot else "LOT"
        self.part_ids = list(part_ids) if part_ids is not None else None

    def _tray_parts(self, tray_index: int) -> list[str]:
        if self.part_ids is not None:
            start = tray_index * self.parts_per_tray
            return self.part_ids[start:start + self.parts_per_tray]
        return [
            f"{self.lot_id}-T{tray_index + 1:02d}P{part:05d}"
            for part in range(self.parts_per_tray)
        ]

    def units(self) -> Iterator[UnitPlan]:
        """Yield one unit per tray."""
        for tray_index in range(self.trays):
            parts = self._tray_parts(tray_index)
            if not parts:
                continue
            tray_id = f"T{tray_index + 1:02d}"
            handler = HandlerSimulator(
                parts, self.handler, self.sites, tray_id=tray_id,
                seed=self.seed + tray_index * 104729,
            )
            yield UnitPlan(
                unit_id=tray_id,
                kind="tray",
                touchdowns=handler.touchdowns(),
                temperature=float(self.handler.temperature),
                change_time_ms=0.0,
                meta={
                    "tray": tray_id,
                    "parts": parts,
                    "index_time_ms": float(self.handler.cycle_time_ms),
                    "tray_capacity": self.parts_per_tray,
                },
            )
