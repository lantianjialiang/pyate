"""Simulated equipment effectiveness.

Times are the handler/tester model times (index, test, jam recovery), not the
wall clock of the computer running the simulation. One touchdown of ``sites``
parts costs ``test_ms + index_ms``. A short tail touchdown still pays that
cycle, so it lowers performance. Jam recovery is downtime, so it lowers
availability. Quality is the pass ratio.

OEE = availability × performance × quality.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EquipmentStats"]


@dataclass
class EquipmentStats:
    """Accumulated OEE inputs for one wafer or tray."""

    sites: int
    tested: int = 0
    passed: int = 0
    touchdowns: int = 0
    productive_ms: float = 0.0
    downtime_ms: float = 0.0
    jams: int = 0
    ideal_test_ms: float | None = None
    index_time_ms: float = 0.0

    def add_touchdown(self, *, tested: int, passed: int, test_ms: float,
                      index_ms: float, jam_ms: float = 0.0) -> None:
        """Record one finished touchdown."""
        self.touchdowns += 1
        self.tested += int(tested)
        self.passed += int(passed)
        self.productive_ms += max(0.0, float(test_ms)) + max(0.0, float(index_ms))
        if float(index_ms) > 0:
            self.index_time_ms = float(index_ms)
        if float(jam_ms) > 0:
            self.jams += 1
            self.downtime_ms += float(jam_ms)
        if int(tested) > 0 and float(test_ms) > 0:
            if self.ideal_test_ms is None or float(test_ms) < self.ideal_test_ms:
                self.ideal_test_ms = float(test_ms)

    @property
    def planned_ms(self) -> float:
        """Productive time plus jam downtime."""
        return self.productive_ms + self.downtime_ms

    @property
    def uph(self) -> float:
        """Net units per hour, after jam downtime."""
        if self.planned_ms <= 0 or self.tested <= 0:
            return 0.0
        return self.tested * 3_600_000.0 / self.planned_ms

    @property
    def gross_uph(self) -> float:
        """Units per hour ignoring jam downtime."""
        if self.productive_ms <= 0 or self.tested <= 0:
            return 0.0
        return self.tested * 3_600_000.0 / self.productive_ms

    @property
    def theoretical_uph(self) -> float:
        """Units per hour if every touchdown fills every site and nothing jams."""
        if self.ideal_test_ms is None or self.sites <= 0:
            return 0.0
        cycle = self.ideal_test_ms + self.index_time_ms
        if cycle <= 0:
            return 0.0
        return self.sites * 3_600_000.0 / cycle

    @property
    def availability(self) -> float:
        """Productive time over productive time plus jam downtime."""
        if self.planned_ms <= 0:
            return 0.0
        return self.productive_ms / self.planned_ms

    @property
    def performance(self) -> float:
        """Actual parts against the parts a full-site cadence would produce."""
        if self.ideal_test_ms is None or self.sites <= 0 or self.productive_ms <= 0 or self.tested <= 0:
            return 0.0
        ideal_part_ms = (self.ideal_test_ms + self.index_time_ms) / self.sites
        if ideal_part_ms <= 0:
            return 0.0
        return min(1.0, (ideal_part_ms * self.tested) / self.productive_ms)

    @property
    def quality(self) -> float:
        """Passed parts over tested parts."""
        if self.tested <= 0:
            return 0.0
        return self.passed / self.tested

    @property
    def oee(self) -> float:
        """Availability × performance × quality, in 0..1."""
        return self.availability * self.performance * self.quality

    def to_dict(self) -> dict[str, float | int]:
        """Rounded figures for events and ``summary.json``."""
        return {
            "tested": self.tested,
            "passed": self.passed,
            "touchdowns": self.touchdowns,
            "jams": self.jams,
            "jam_ms": round(self.downtime_ms, 3),
            "productive_ms": round(self.productive_ms, 3),
            "uph": round(self.uph, 2),
            "gross_uph": round(self.gross_uph, 2),
            "theoretical_uph": round(self.theoretical_uph, 2),
            "availability": round(self.availability, 4),
            "performance": round(self.performance, 4),
            "quality": round(self.quality, 4),
            "oee": round(self.oee, 4),
        }
