"""CP (wafer probe) scenario."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..config.models import WaferConfig
from .base import UnitPlan
from .prober_sim import ProberSimulator
from .wafer_map import WaferMap

__all__ = ["CpScenario"]


class CpScenario:
    """One lot of wafers tested on a prober."""

    name = "CP"

    def __init__(self, session: Any, sites: int, *, wafers: list[str] | None = None,
                 die_filter: set[tuple[str, int, int]] | None = None,
                 seed: int | None = None) -> None:
        self.session = session
        self.sites = sites
        self.seed = seed if seed is not None else int(getattr(session.run, "seed", 0))
        lot = getattr(session, "lot", None)
        wafer_cfg = getattr(session, "wafer", None)
        if wafer_cfg is None:
            raise ValueError("CP scenario requires wafer.yaml")
        self.wafer_config: WaferConfig = wafer_cfg
        self.wafers = list(wafers or (lot.wafers if lot and lot.wafers else ["W01"]))
        self.die_filter = die_filter
        self.prober = getattr(session, "prober", None)
        if self.prober is None:
            raise ValueError("CP scenario requires prober.yaml")

    def units(self) -> Iterator[UnitPlan]:
        """Yield one unit per wafer."""
        assert self.prober is not None
        for index, wafer_id in enumerate(self.wafers):
            wafer = WaferMap(self.wafer_config, wafer_id)
            prober = ProberSimulator(
                wafer, self.prober, self.sites,
                seed=self.seed + index * 7919,
            )
            yield UnitPlan(
                unit_id=wafer_id,
                kind="wafer",
                touchdowns=prober.touchdowns(),
                temperature=float(self.prober.temperature),
                change_time_ms=float(self.prober.wafer_change_time_s) * 1000.0,
                meta={
                    "wafer_map": wafer,
                    "touchdown_count": prober.touchdown_count(),
                    "die_filter": self.die_filter,
                    "index_time_ms": float(self.prober.index_time_ms),
                },
            )
