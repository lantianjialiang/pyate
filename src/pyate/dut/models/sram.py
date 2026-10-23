"""Small synchronous single-port SRAM used by the March test item."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, DUTError, PinSpec

__all__ = ["SramModel"]


class SramModel(DUTModel):
    """Synchronous single-port SRAM (active-low ``CEN``/``WEN``).

    The model also exposes a register-accurate Python API
    (:meth:`sram_read` / :meth:`sram_write`) so the March item can exercise
    the array without writing a huge vector set.
    """

    model_name = "sram"
    description = "Single-port synchronous SRAM"
    defaults = {
        **DUTModel.defaults,
        "depth": 64,
        "width": 8,
        "base_period_ns": 10.0,
        "idd_static_ua": 300.0,
    }

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.depth = int((params or {}).get("depth", 64))
        self.width = int((params or {}).get("width", 8))
        if self.depth < 2 or (self.depth & (self.depth - 1)):
            raise ValueError("sram depth must be a power of two >= 2")
        if not 1 <= self.width <= 32:
            raise ValueError("sram width must be in 1..32")
        self.addr_bits = (self.depth - 1).bit_length()
        self.data_mask = (1 << self.width) - 1
        self._mem = [0] * self.depth
        self._q = 0
        super().__init__(params, rng)

    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "RSTN": PinSpec("RSTN", "in"),
            "CEN": PinSpec("CEN", "in"),
            "WEN": PinSpec("WEN", "in"),
        }
        for index in range(self.addr_bits):
            pins[f"A{index}"] = PinSpec(f"A{index}", "in", bus="A")
        for index in range(self.width):
            pins[f"D{index}"] = PinSpec(f"D{index}", "in", bus="D")
            pins[f"Q{index}"] = PinSpec(f"Q{index}", "out", bus="Q")
        return pins

    def reset(self) -> None:
        """Clear memory contents (power-on state)."""
        self._mem = [0] * self.depth
        self._q = 0
        self._eval_comb()

    def _eval_comb(self) -> None:
        for index in range(self.width):
            self._out[f"Q{index}"] = (self._q >> index) & 1

    def _address(self) -> int:
        return sum(self._inputs.get(f"A{i}", 0) << i for i in range(self.addr_bits)) % self.depth

    def _data(self) -> int:
        return sum(self._inputs.get(f"D{i}", 0) << i for i in range(self.width)) & self.data_mask

    def _clock_edge(self) -> None:
        if self._inputs.get("CEN", 1) == 0:
            addr = self._address()
            if self._inputs.get("WEN", 1) == 0:
                self._mem[addr] = self._data()
            else:
                self._q = self._mem[addr]
        self._eval_comb()

    # ------------------------------------------------------------- debug API
    def sram_write(self, address: int, data: int) -> None:
        """Directly write one word (bypasses the pin interface)."""
        if not 0 <= address < self.depth:
            raise DUTError(f"address {address} outside 0..{self.depth - 1}")
        self._mem[address] = data & self.data_mask

    def sram_read(self, address: int) -> int:
        """Directly read one word."""
        if not 0 <= address < self.depth:
            raise DUTError(f"address {address} outside 0..{self.depth - 1}")
        return self._mem[address]

    def contents(self) -> list[int]:
        """Copy of the memory array."""
        return list(self._mem)
