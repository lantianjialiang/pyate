"""N-bit serial-in / parallel-out shift register (scan-chain skeleton)."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, PinSpec

__all__ = ["ShiftRegModel"]


class ShiftRegModel(DUTModel):
    """Rising-edge shift register.

    ``SI`` is sampled on every edge, ``Q0`` is the newest bit, ``SO`` the
    oldest one, ``Q0..Q{n-1}`` expose the full chain in parallel.
    """

    model_name = "shiftreg"
    description = "N-bit SIPO shift register"
    defaults = {**DUTModel.defaults, "bits": 8, "reset_value": 0}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.bits = int((params or {}).get("bits", 8))
        if not 1 <= self.bits <= 64:
            raise ValueError("shiftreg bits must be in 1..64")
        self._mask = (1 << self.bits) - 1
        self._state = 0
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "RSTN": PinSpec("RSTN", "in"),
            "SI": PinSpec("SI", "in"),
            "SO": PinSpec("SO", "out"),
        }
        for index in range(self.bits):
            pins[f"Q{index}"] = PinSpec(f"Q{index}", "out", bus="Q")
        return pins

    def reset(self) -> None:
        """Clear the chain."""
        self._state = int(self.params.get("reset_value", 0)) & self._mask
        self._eval_comb()

    def _eval_comb(self) -> None:
        for index in range(self.bits):
            self._out[f"Q{index}"] = (self._state >> index) & 1
        self._out["SO"] = (self._state >> (self.bits - 1)) & 1

    def _clock_edge(self) -> None:
        if self._inputs.get("RSTN", 1) == 0:
            self._state = 0
        else:
            self._state = ((self._state << 1) | (self._inputs.get("SI", 0) & 1)) & self._mask
        self._eval_comb()

    @property
    def value(self) -> int:
        """Current parallel content of the chain."""
        return self._state
