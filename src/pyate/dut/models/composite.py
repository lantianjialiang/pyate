"""Reference composite DUT: 8-bit counter + 8-bit shift register."""

from __future__ import annotations

import random
from typing import Any, Mapping

from ..base import DUTModel, PinSpec
from .counter import CounterModel
from .shiftreg import ShiftRegModel

__all__ = ["CompositeModel"]


class CompositeModel(DUTModel):
    """Counter and shift register sharing ``CLK``/``RSTN``.

    Pins: ``CLK``, ``RSTN``, ``EN``, ``SI``; outputs ``Q0..Qn-1`` (counter),
    ``S0..Sm-1`` (shift register, ``S0`` newest) and ``SO`` (chain output).
    """

    model_name = "composite"
    description = "8-bit counter + 8-bit shift register"
    defaults = {**DUTModel.defaults, "counter_bits": 8, "shift_bits": 8}

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        raw = dict(params or {})
        self.counter_bits = int(raw.get("counter_bits", raw.get("bits", 8)))
        self.shift_bits = int(raw.get("shift_bits", 8))
        super().__init__(params, rng)
        child_rng = random.Random(self.rng.randrange(1 << 30))
        self._counter = CounterModel({"bits": self.counter_bits}, random.Random(child_rng.randrange(1 << 30)))
        self._shift = ShiftRegModel({"bits": self.shift_bits}, random.Random(child_rng.randrange(1 << 30)))
        self._counter.power(self.vdd_nom)
        self._shift.power(self.vdd_nom)
        self._sync_outputs()

    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "RSTN": PinSpec("RSTN", "in"),
            "EN": PinSpec("EN", "in"),
            "SI": PinSpec("SI", "in"),
        }
        for index in range(self.counter_bits):
            pins[f"Q{index}"] = PinSpec(f"Q{index}", "out", bus="Q")
        for index in range(self.shift_bits):
            pins[f"S{index}"] = PinSpec(f"S{index}", "out", bus="S")
        pins["SO"] = PinSpec("SO", "out")
        return pins

    def reset(self) -> None:
        """Reset both sub-blocks."""
        has_children = hasattr(self, "_counter")
        if has_children:
            self._counter.reset()
            self._shift.reset()
            self._sync_outputs()
        else:
            for pin in self._out:
                self._out[pin] = 0

    def power(self, vdd: float) -> None:
        """Power both sub-blocks with the same supply."""
        super().power(vdd)
        if hasattr(self, "_counter"):
            for child in (self._counter, self._shift):
                if vdd > 0:
                    child.power(vdd)
                else:
                    child.power(0.0)

    def _sync_outputs(self) -> None:
        counter_state = self._counter.value
        shift_state = self._shift.value
        for index in range(self.counter_bits):
            self._out[f"Q{index}"] = (counter_state >> index) & 1
        for index in range(self.shift_bits):
            self._out[f"S{index}"] = (shift_state >> index) & 1
        self._out["SO"] = (shift_state >> (self.shift_bits - 1)) & 1

    def _eval_comb(self) -> None:
        self._sync_outputs()

    def _clock_edge(self) -> None:
        self._counter.set_inputs({k: self._inputs[k] for k in ("CLK", "RSTN", "EN")})
        self._shift.set_inputs({k: self._inputs[k] for k in ("CLK", "RSTN", "SI")})
        self._counter.clock_cycle()
        self._shift.clock_cycle()
        self._sync_outputs()

    @property
    def counter_value(self) -> int:
        """Current counter value."""
        return self._counter.value

    @property
    def shift_value(self) -> int:
        """Current shift-register content."""
        return self._shift.value

    def min_period_ns(self, vdd: float | None = None) -> float:
        """Slowest of both sub-blocks."""
        supply = self._vdd if vdd is None else vdd
        return max(self._counter.min_period_ns(supply), self._shift.min_period_ns(supply))

    def apply_process_variation(self, *, vth_shift: float = 0.0, idd_scale: float = 1.0,
                                leak_scale: float = 1.0) -> None:
        """Propagate variation to both sub-blocks."""
        super().apply_process_variation(vth_shift=vth_shift, idd_scale=idd_scale,
                                        leak_scale=leak_scale)
        for child in (self._counter, self._shift):
            child.apply_process_variation(vth_shift=vth_shift, idd_scale=idd_scale,
                                          leak_scale=leak_scale)
