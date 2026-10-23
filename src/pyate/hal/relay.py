"""Relay / DIB matrix abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["Relay"]


class Relay(ABC):
    """Named relays used to route instruments to DUT pins."""

    @abstractmethod
    def close(self, name: str) -> None:
        """Close (connect) a relay."""

    @abstractmethod
    def open(self, name: str) -> None:
        """Open (disconnect) a relay."""

    @abstractmethod
    def is_closed(self, name: str) -> bool:
        """State of the relay."""

    @abstractmethod
    def open_all(self) -> None:
        """Open every relay (safe state)."""
