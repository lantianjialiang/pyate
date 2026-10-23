"""Combinational logic gate arrays: inverter / nand / nor / xor / and / or."""

from __future__ import annotations

import random
from typing import Any, Callable, Mapping

from ..base import DUTModel, PinSpec

__all__ = ["GateArrayModel"]

_GATES: dict[str, Callable[[int, int], int]] = {
    "buf": lambda a, _b: a,
    "inv": lambda a, _b: 0 if a else 1,
    "and": lambda a, b: a & b,
    "nand": lambda a, b: 0 if (a & b) else 1,
    "or": lambda a, b: a | b,
    "nor": lambda a, b: 0 if (a | b) else 1,
    "xor": lambda a, b: a ^ b,
    "xnor": lambda a, b: 0 if (a ^ b) else 1,
}

_TWO_INPUT = {"and", "nand", "or", "nor", "xor", "xnor"}


class GateArrayModel(DUTModel):
    """An array of identical gates, one per bit slice."""

    model_name = "gates"
    description = "Combinational gate array"
    defaults = {**DUTModel.defaults, "gate": "nand", "bits": 4, "base_period_ns": 4.0}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.gate = str((params or {}).get("gate", "nand")).lower()
        if self.gate not in _GATES:
            raise ValueError(f"unknown gate '{self.gate}' (choose from {sorted(_GATES)})")
        self.bits = int((params or {}).get("bits", 4))
        self.two_input = self.gate in _TWO_INPUT
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins: dict[str, PinSpec] = {}
        for index in range(self.bits):
            pins[f"A{index}"] = PinSpec(f"A{index}", "in")
            if self.two_input:
                pins[f"B{index}"] = PinSpec(f"B{index}", "in")
            pins[f"Y{index}"] = PinSpec(f"Y{index}", "out")
        return pins

    def reset(self) -> None:
        """No state to reset in a combinational model."""
        self._eval_comb()

    def _eval_comb(self) -> None:
        func = _GATES[self.gate]
        for index in range(self.bits):
            a = self._inputs.get(f"A{index}", 0)
            b = self._inputs.get(f"B{index}", 0) if self.two_input else 0
            self._out[f"Y{index}"] = func(a, b)
