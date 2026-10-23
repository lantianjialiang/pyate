"""Parametric measurement unit abstraction (FVMI / FIMV)."""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["PMU"]


class PMU(ABC):
    """Force voltage / measure current or force current / measure voltage."""

    @abstractmethod
    def force_voltage(self, pin: str, volts: float, irange: float = 1e-3) -> None:
        """FVMI mode: force ``volts`` and measure the resulting current."""

    @abstractmethod
    def force_current(self, pin: str, ampere: float, vclamp: float = 5.0) -> None:
        """FIMV mode: force ``ampere`` and measure the resulting voltage."""

    @abstractmethod
    def measure_current(self, pin: str) -> float:
        """Measure current at ``pin`` (ampere, positive into the DUT)."""

    @abstractmethod
    def measure_voltage(self, pin: str) -> float:
        """Measure voltage at ``pin`` (volt)."""

    @abstractmethod
    def connect(self, pin: str) -> None:
        """Route the PMU to ``pin``."""

    @abstractmethod
    def disconnect(self, pin: str) -> None:
        """Unroute the PMU from ``pin``."""

    @abstractmethod
    def is_connected(self, pin: str) -> bool:
        """Routing state of ``pin``."""
