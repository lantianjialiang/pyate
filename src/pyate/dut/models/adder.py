"""N-bit combinational ripple-carry adder."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, PinSpec

__all__ = ["AdderModel"]


class AdderModel(DUTModel):
    """``S = A + B + CIN`` with carry out."""

    model_name = "adder"
    description = "N-bit combinational adder"
    defaults = {**DUTModel.defaults, "bits": 8, "base_period_ns": 8.0}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.bits = int((params or {}).get("bits", 8))
        if not 1 <= self.bits <= 32:
            raise ValueError("adder bits must be in 1..32")
        self._mask = (1 << self.bits) - 1
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins: dict[str, PinSpec] = {"CIN": PinSpec("CIN", "in")}
        for index in range(self.bits):
            pins[f"A{index}"] = PinSpec(f"A{index}", "in")
            pins[f"B{index}"] = PinSpec(f"B{index}", "in")
            pins[f"S{index}"] = PinSpec(f"S{index}", "out")
        pins["COUT"] = PinSpec("COUT", "out")
        return pins

    def reset(self) -> None:
        """Combinational model: just recompute."""
        self._eval_comb()

    def _eval_comb(self) -> None:
        a = sum(self._inputs.get(f"A{i}", 0) << i for i in range(self.bits))
        b = sum(self._inputs.get(f"B{i}", 0) << i for i in range(self.bits))
        total = a + b + self._inputs.get("CIN", 0)
        for index in range(self.bits):
            self._out[f"S{index}"] = (total >> index) & 1
        self._out["COUT"] = (total >> self.bits) & 1
