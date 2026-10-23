"""Scenario protocol: a unit (wafer / tray) that yields touchdowns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..engine.context import TouchdownInfo

__all__ = ["UnitPlan", "Scenario"]


@dataclass
class UnitPlan:
    """One wafer or one tray to be tested."""

    unit_id: str
    kind: str
    touchdowns: Iterable[TouchdownInfo]
    temperature: float = 25.0
    change_time_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class Scenario(Protocol):
    """Anything that can produce :class:`UnitPlan` objects."""

    name: str

    def units(self) -> Iterable[UnitPlan]:
        """Iterate the units of this lot."""
