"""Timing controller abstraction and small timing helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config.models import TimingSet

__all__ = ["TimingController", "TimingPlan"]


@dataclass(frozen=True)
class TimingPlan:
    """Resolved timing for one vector, in nanoseconds."""

    period_ns: float
    drive_on_ns: float
    data_ns: float
    return_ns: float
    strobe_ns: float

    @classmethod
    def from_set(cls, timing: TimingSet) -> "TimingPlan":
        """Build a plan from a configured timing set."""
        edges = timing.edges
        return cls(
            period_ns=timing.period_ns,
            drive_on_ns=edges.drive_on,
            data_ns=edges.data,
            return_ns=edges.return_,
            strobe_ns=edges.compare,
        )

    def frequency_mhz(self) -> float:
        """Vector rate implied by this plan."""
        return 1000.0 / self.period_ns


class TimingController(ABC):
    """Timing set management."""

    @abstractmethod
    def apply(self, timing: TimingSet) -> None:
        """Select a timing set."""

    @abstractmethod
    def plan(self) -> TimingPlan:
        """Current resolved timing."""
