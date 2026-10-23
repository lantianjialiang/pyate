"""Fibonacci LFSR pseudo-random sequence generator."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, PinSpec

__all__ = ["LfsrModel"]


class LfsrModel(DUTModel):
    """N-bit Fibonacci LFSR shifting right, feedback on the tapped bits."""

    model_name = "lfsr"
    description = "N-bit Fibonacci LFSR"
    defaults = {**DUTModel.defaults, "bits": 8, "seed_value": 0xAC, "taps": [8, 6, 5, 4]}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.bits = int((params or {}).get("bits", 8))
        raw_taps = (params or {}).get("taps", [self.bits, 6, 5, 4])
        taps = sorted({int(t) for t in raw_taps if 1 <= int(t) <= self.bits})
        self.taps = taps or [self.bits]
        self._mask = (1 << self.bits) - 1
        self._seed_value = int((params or {}).get("seed_value", 0xAC)) & self._mask or 1
        self._state = self._seed_value
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "RSTN": PinSpec("RSTN", "in"),
            "SO": PinSpec("SO", "out"),
        }
        for index in range(self.bits):
            pins[f"Q{index}"] = PinSpec(f"Q{index}", "out", bus="Q")
        return pins

    def reset(self) -> None:
        """Load the configured seed value."""
        self._state = self._seed_value
        self._eval_comb()

    def _eval_comb(self) -> None:
        for index in range(self.bits):
            self._out[f"Q{index}"] = (self._state >> index) & 1
        self._out["SO"] = self._state & 1

    def _feedback(self) -> int:
        parity = 0
        for tap in self.taps:
            parity ^= (self._state >> (tap - 1)) & 1
        return parity

    def _clock_edge(self) -> None:
        if self._inputs.get("RSTN", 1) == 0:
            self._state = self._seed_value
        else:
            feedback = self._feedback()
            self._state = (self._state >> 1) | (feedback << (self.bits - 1))
        self._eval_comb()

    @property
    def value(self) -> int:
        """Current LFSR state."""
        return self._state
