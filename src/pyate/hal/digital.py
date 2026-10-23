"""Digital channel abstraction (levels, timing, pattern execution)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..config.models import LevelsSet, TimingSet
    from ..dut.base import DUTModel
    from ..pattern.model import Pattern, PatternResult

__all__ = ["DigitalChannel"]


class DigitalChannel(ABC):
    """One site's digital pin electronics."""

    dut: "DUTModel | None"

    @abstractmethod
    def apply_levels(self, levels: "LevelsSet") -> None:
        """Select the level set used for drive and compare."""

    @abstractmethod
    def apply_timing(self, timing: "TimingSet") -> None:
        """Select the timing set."""

    @abstractmethod
    def load_pattern(self, pattern: "Pattern | str | Any") -> "Pattern":
        """Load a parsed pattern, a path or raw text."""

    @abstractmethod
    def set_vdd(self, volts: float) -> None:
        """Program the DUT supply (also used by shmoo/speed search)."""

    @abstractmethod
    def run(self, *, start_label: str | None = None, stop_on_first_fail: bool = True,
            max_cycles: int | None = None, capture_start: int | None = None,
            capture_stop: int | None = None) -> "PatternResult":
        """Execute the loaded pattern and return the compare result."""
