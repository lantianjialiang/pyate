"""Device power supply abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["DPS"]


class DPS(ABC):
    """Programmable device power supply (force voltage, measure current)."""

    @abstractmethod
    def set_voltage(self, pin: str, volts: float) -> None:
        """Program the output voltage of ``pin``."""

    @abstractmethod
    def set_current_limit(self, pin: str, ampere: float) -> None:
        """Program the current compliance of ``pin``."""

    @abstractmethod
    def connect(self, pin: str) -> None:
        """Connect the supply output to the DUT pin."""

    @abstractmethod
    def disconnect(self, pin: str) -> None:
        """Disconnect the supply output."""

    @abstractmethod
    def measure_current(self, pin: str) -> float:
        """Measure the current flowing into ``pin`` (ampere)."""

    @abstractmethod
    def measure_voltage(self, pin: str) -> float:
        """Measure the voltage actually seen at ``pin``."""

    @abstractmethod
    def is_connected(self, pin: str) -> bool:
        """Connection state of ``pin``."""
