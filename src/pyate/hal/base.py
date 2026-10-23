"""HAL exceptions and aggregate interfaces.

Test items only ever talk to these abstractions; swapping in a real
instrument driver means implementing :class:`HALBackend` again.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..dut.base import DUTModel
    from .digital import DigitalChannel
    from .dps import DPS
    from .pmu import PMU
    from .relay import Relay

__all__ = ["HALError", "NotConnected", "OverCurrent", "ResourceConflict",
           "SelfCheckError", "SiteResources", "HALBackend"]


class HALError(Exception):
    """Base class for hardware abstraction errors."""

    def __init__(self, message: str, *, site: int | None = None, pin: str | None = None) -> None:
        self.site = site
        self.pin = pin
        prefix = []
        if site is not None:
            prefix.append(f"site {site}")
        if pin is not None:
            prefix.append(f"pin {pin}")
        self.message = f"[{', '.join(prefix)}] {message}" if prefix else message
        super().__init__(self.message)


class NotConnected(HALError):
    """Raised when an instrument is used before ``connect()``."""


class OverCurrent(HALError):
    """Raised when a supply or PMU exceeds its programmed current limit."""


class ResourceConflict(HALError):
    """Raised when two logical pins map to the same physical resource."""


class SelfCheckError(HALError):
    """Raised when the tester self test fails."""


@dataclass
class SiteResources:
    """All instruments belonging to one site."""

    site_id: int
    dps: "DPS"
    pmu: "PMU"
    relay: "Relay"
    digital: "DigitalChannel"

    def dut(self) -> "DUTModel | None":
        """DUT bound to the digital channel (may be ``None`` before binding)."""
        return getattr(self.digital, "dut", None)


class HALBackend(ABC):
    """Aggregate tester interface."""

    @abstractmethod
    def site(self, site_id: int) -> SiteResources:
        """Return the resources of ``site_id`` (1-based)."""

    @abstractmethod
    def bind_dut(self, site_id: int, dut: "DUTModel") -> None:
        """Attach a DUT instance to a site."""

    @abstractmethod
    def reset(self) -> None:
        """Open all relays and disconnect every instrument."""

    @abstractmethod
    def self_check(self) -> list[str]:
        """Run a quick self test, returning human readable problems."""

    @abstractmethod
    def elapsed_ms(self, site_id: int) -> float:
        """Simulated (or real) time spent on ``site_id``."""

    @abstractmethod
    def reset_clock(self, site_id: int | None = None) -> None:
        """Reset the per-site time accumulator."""

    def describe(self) -> dict[str, Any]:
        """Backend description for logs."""
        return {"backend": type(self).__name__}
