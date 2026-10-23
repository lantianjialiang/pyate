"""Synchronous N-bit counter with enable and active-low reset."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, PinSpec

__all__ = ["CounterModel"]


class CounterModel(DUTModel):
    """Rising-edge 8-bit (configurable) binary counter.

    Pins: ``CLK``, ``RSTN`` (sync, active low), ``EN``, ``Q0..Q{n-1}``.
    """

    model_name = "counter"
    description = "N-bit synchronous counter"
    defaults = {**DUTModel.defaults, "bits": 8}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.bits = int((params or {}).get("bits", 8))
        if not 1 <= self.bits <= 32:
            raise ValueError("counter bits must be in 1..32")
        self._mask = (1 << self.bits) - 1
        self._count = 0
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "RSTN": PinSpec("RSTN", "in"),
            "EN": PinSpec("EN", "in"),
        }
        for index in range(self.bits):
            pins[f"Q{index}"] = PinSpec(f"Q{index}", "out", bus="Q")
        return pins

    def reset(self) -> None:
        """Clear the counter (power-on state)."""
        self._count = 0
        for index in range(self.bits):
            self._out[f"Q{index}"] = 0

    def _eval_comb(self) -> None:
        for index in range(self.bits):
            self._out[f"Q{index}"] = (self._count >> index) & 1

    def _clock_edge(self) -> None:
        if self._inputs.get("RSTN", 1) == 0:
            self._count = 0
        elif self._inputs.get("EN", 0) == 1:
            self._count = (self._count + 1) & self._mask
        self._eval_comb()

    @property
    def value(self) -> int:
        """Current counter value."""
        return self._count

    def load(self, value: int) -> None:
        """Force the counter to ``value`` (used by tests and scan debug)."""
        self._count = value & self._mask
        self._eval_comb()
