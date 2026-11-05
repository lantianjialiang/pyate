"""A small successive-approximation ADC model used by the reference plugin."""

from __future__ import annotations

import math
import random
from typing import Any, Mapping

from pyate.dut.base import DUTModel, PinSpec

__all__ = ["AdcModel"]


class AdcModel(DUTModel):
    """8-bit (configurable) SAR ADC with a settling input node.

    Pins: ``CLK``, ``START``, ``RDY`` (conversion done) and ``D0..D{n-1}``.
    The analog input is modelled as a parameter (``vin``) that a test item may
    change through :meth:`set_vin`; the digital result appears on the ``D`` bus
    after ``conv_clocks`` clock edges.

    Linearity is modelled with a smooth INL bow (``inl_lsb``) plus per-code
    random error (``dnl_lsb``), so an INL/DNL test has something real to measure.
    """

    model_name = "adc"
    description = "Successive-approximation ADC (digital output bus)"
    defaults = {
        **DUTModel.defaults,
        "bits": 8,
        "vref": 3.3,
        "vin": 1.65,
        "conv_clocks": 8,
        "inl_lsb": 0.6,
        "dnl_lsb": 0.25,
        "idd_static_ua": 420.0,
    }

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        merged = dict(params or {})
        bits = int(merged.get("bits", 8))
        if not 4 <= bits <= 16:
            raise ValueError("adc bits must be in 4..16")
        self.bits = bits
        self._mask = (1 << bits) - 1
        self.vref = float(merged.get("vref", 3.3))
        self.conv_clocks = int(merged.get("conv_clocks", bits))
        self.vin = float(merged.get("vin", self.vref / 2.0))
        self.inl_lsb = float(merged.get("inl_lsb", 0.6))
        self.dnl_lsb = float(merged.get("dnl_lsb", 0.25))
        self._phase = 0
        self._code = 0
        self._ready = 0
        super().__init__(merged, rng)
        # deviation per code, fixed for the life of the part (mismatch)
        self._code_error = [
            self.rng.gauss(0.0, self.dnl_lsb / 3.0) if self.dnl_lsb else 0.0
            for _ in range(self._mask + 1)
        ]

    # ------------------------------------------------------------------ pins
    def _build_pins(self) -> dict[str, PinSpec]:
        pins = {
            "CLK": PinSpec("CLK", "in", diode=False),
            "START": PinSpec("START", "in"),
            "RDY": PinSpec("RDY", "out"),
        }
        for index in range(self.bits):
            pins[f"D{index}"] = PinSpec(f"D{index}", "out", bus="D")
        return pins

    # --------------------------------------------------------------- convert
    def ideal_code(self, vin: float) -> int:
        """Code a perfect ADC would return for ``vin``."""
        volts = min(max(vin, 0.0), self.vref)
        return int(round(volts / self.vref * self._mask))

    def code_for(self, vin: float) -> int:
        """Code this (non-ideal) part returns, including INL bow and mismatch."""
        ideal = self.ideal_code(vin)
        if not self.is_functional():
            return self._mask // 3  # a dead part latches a fixed code
        bow = 0.0
        if self.inl_lsb:
            # smooth INL: peak error at mid-scale
            bow = self.inl_lsb * math.sin(math.pi * ideal / max(1, self._mask))
        mismatch = self._code_error[ideal] if self._code_error else 0.0
        code = int(round(ideal + bow + mismatch + self.rng.gauss(0.0, 0.05)))
        return min(max(code, 0), self._mask)

    def set_vin(self, volts: float) -> None:
        """Drive the analog input (volts)."""
        self.vin = float(min(max(volts, 0.0), self.vref * 1.1))
        self._code = self.code_for(self.vin)
        self._eval_comb()

    def convert(self, volts: float | None = None) -> int:
        """Run one conversion and return the resulting code."""
        if volts is not None:
            self.set_vin(volts)
        return self._code

    # ----------------------------------------------------------- simulation
    def reset(self) -> None:
        """Power-on reset: no valid result yet."""
        self._phase = 0
        self._code = 0
        self._ready = 0
        self._eval_comb()

    def _eval_comb(self) -> None:
        for index in range(self.bits):
            self._out[f"D{index}"] = (self._code >> index) & 1
        self._out["RDY"] = self._ready

    def _clock_edge(self) -> None:
        if self._inputs.get("START", 0) == 1:
            self._phase = 0
            self._ready = 0
            self._eval_comb()
            return
        self._phase += 1
        if self._phase >= self.conv_clocks:
            self._code = self.code_for(self.vin)
            self._ready = 1
            self._phase = 0
        self._eval_comb()

    # -------------------------------------------------------- measurements
    def supply_current(self, vdd: float | None = None, *, freq_mhz: float = 0.0,
                       activity: float = 1.0) -> float:
        """SA converter: static bias plus a small dynamic term."""
        return super().supply_current(vdd, freq_mhz=freq_mhz * 0.5, activity=activity)

    def min_period_ns(self, vdd: float | None = None) -> float:
        """Conversions need a slightly longer clock than a pure logic part."""
        return super().min_period_ns(vdd) * 1.25
