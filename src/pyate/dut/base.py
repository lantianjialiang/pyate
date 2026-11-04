"""DUT behaviour model base class and defect injection framework.

A DUT model is a *digital* model: it exposes flat pins, a combinational
``evaluate`` and a synchronous ``clock_cycle``.  Analog-ish observables
(supply current, pin leakage, protection diode) are derived from the same
state so that DPS/PMU measurements stay physically plausible.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from .identity import DUTInstanceSpec

__all__ = ["DUTError", "DUTModel", "Defect", "PinSpec"]


class DUTError(Exception):
    """Raised for invalid DUT operations (unknown pin, bad supply, ...)."""


@dataclass(frozen=True)
class PinSpec:
    """Static description of one DUT pin."""

    name: str
    direction: str = "io"  # in | out | io | power
    diode: bool = True
    bus: str | None = None

    @property
    def is_input(self) -> bool:
        """True for pins the tester may drive."""
        return self.direction in ("in", "io")

    @property
    def is_output(self) -> bool:
        """True for pins the tester may compare."""
        return self.direction in ("out", "io")


@dataclass
class Defect:
    """One injected manufacturing defect."""

    kind: str
    pin: str | None = None
    pin2: str | None = None
    value: int | None = None
    prob: float = 1.0
    factor: float = 1.0
    na: float = 100.0

    def describe(self) -> str:
        """Short human readable form (used in logs and reports)."""
        if self.kind == "stuck_at":
            return f"stuck_at({self.pin}={self.value})"
        if self.kind == "short":
            return f"short({self.pin},{self.pin2})"
        if self.kind == "leakage":
            return f"leakage({self.pin},{self.na:.0f}nA)"
        if self.kind == "high_idd":
            return f"high_idd(x{self.factor:g})"
        if self.kind == "slow":
            return f"slow(x{self.factor:g})"
        if self.kind == "random_bitflip":
            return f"random_bitflip(p={self.prob:g})"
        return f"{self.kind}({self.pin})"


class DUTModel(ABC):
    """Base class for all built-in DUT behaviour models."""

    model_name: ClassVar[str] = "base"
    description: ClassVar[str] = "abstract DUT"

    #: electrical defaults, overridable through ``dut_profile.params``
    defaults: ClassVar[dict[str, Any]] = {
        "vdd_nom": 3.3,
        "vdd_min": 2.7,
        "vdd_max": 3.6,
        "idd_static_ua": 180.0,
        "idd_dynamic_nf": 0.02,
        "leakage_na": 50.0,
        "diode_v": 0.68,
        "base_period_ns": 12.0,
        "vth_v": 2.0,
        "temperature_ref_c": 25.0,
        "idd_tempco_per_c": 0.002,
        "leak_tempco_per_c": math.log(2.0) / 20.0,
        "period_tempco_per_c": 0.001,
        "vmin_tempco_v_per_c": 0.0005,
        "diode_tempco_v_per_c": -0.002,
        "output_delay_fraction": 0.2,
    }

    def __init__(self, params: Mapping[str, Any] | None = None,
                 rng: random.Random | None = None) -> None:
        self.params: dict[str, Any] = dict(self.defaults)
        self.instance_spec: DUTInstanceSpec | None = None
        self.noise_seed: int | None = None
        self.temperature_c = float(self.defaults["temperature_ref_c"])
        self.params.update(dict(params or {}))
        self.rng = rng or random.Random(0)
        self.pins: dict[str, PinSpec] = self._build_pins()
        self.vdd_nom = float(self.params["vdd_nom"])
        self.vdd_min = float(self.params["vdd_min"])
        self.vdd_max = float(self.params["vdd_max"])
        self._vdd = 0.0
        self._powered = False
        self._inputs: dict[str, int] = {name: 0 for name, pin in self.pins.items() if pin.is_input}
        self._out: dict[str, int | None] = {name: 0 for name, pin in self.pins.items() if pin.is_output}
        self.defects: list[Defect] = []
        # process variation
        self.vth_shift = 0.0
        self.idd_scale = 1.0
        self.leak_scale = 1.0
        self.slow_derate = 1.0
        self.diode_v = float(self.params["diode_v"]) + self.rng.uniform(-0.02, 0.02)
        self._defect_leak: dict[str, float] = {}
        self._output_stuck: dict[str, int] = {}
        self._input_stuck: dict[str, int] = {}
        self._opens: set[str] = set()
        self._shorts: list[tuple[str, str]] = []
        self._high_idd = 1.0
        self._bitflip = 0.0
        self._clock_cycles = 0
        self.reset()

    # ------------------------------------------------------------- interface
    @abstractmethod
    def _build_pins(self) -> dict[str, PinSpec]:
        """Declare every pin of the DUT."""

    @abstractmethod
    def _eval_comb(self) -> None:
        """Compute combinational outputs into ``self._out``."""

    def _clock_edge(self) -> None:
        """Synchronous behaviour; default is purely combinational."""
        self._eval_comb()

    # ------------------------------------------------------------------ pins
    @property
    def input_pins(self) -> list[str]:
        """All drivable pins."""
        return [name for name, pin in self.pins.items() if pin.is_input]

    @property
    def output_pins(self) -> list[str]:
        """All comparable pins."""
        return [name for name, pin in self.pins.items() if pin.is_output]

    @property
    def signal_pins(self) -> list[str]:
        """Pins that are not supply pins."""
        return [name for name, pin in self.pins.items() if pin.direction != "power"]

    def has_pin(self, name: str) -> bool:
        """True when ``name`` is a declared pin."""
        return name in self.pins

    def check_pin(self, name: str) -> PinSpec:
        """Return the pin spec or raise :class:`DUTError`."""
        try:
            return self.pins[name]
        except KeyError as exc:
            raise DUTError(f"unknown DUT pin '{name}'") from exc

    # ----------------------------------------------------------------- power
    def power(self, vdd: float) -> None:
        """Apply (or remove, with ``vdd=0``) the supply."""
        if vdd < 0:
            raise DUTError(f"negative supply voltage {vdd}")
        self._vdd = float(vdd)
        self._powered = self._vdd >= 0.5
        if not self._powered:
            for pin in self._out:
                self._out[pin] = None
            self.reset()
        else:
            self._eval_comb()

    @property
    def vdd(self) -> float:
        """Currently applied supply voltage."""
        return self._vdd

    @property
    def powered(self) -> bool:
        """True when the DUT sees a usable supply."""
        return self._powered

    @property
    def vdd_min_effective(self) -> float:
        """Minimum working voltage including process shift."""
        return self.vdd_min + self.vth_shift + float(self.params["vmin_tempco_v_per_c"]) * self.temperature_delta

    @property
    def temperature_delta(self) -> float:
        """Difference from the model's characterized reference temperature."""
        return self.temperature_c - float(self.params["temperature_ref_c"])

    def set_temperature(self, celsius: float) -> None:
        """Set the operating corner without resampling manufacturing variation."""
        if not math.isfinite(celsius) or celsius < -273.15:
            raise DUTError("temperature must be finite and above absolute zero")
        self.temperature_c = float(celsius)

    def _thermal_scale(self, coefficient: str) -> float:
        # Bound the exponential so malformed/extreme corners cannot overflow.
        exponent = float(self.params[coefficient]) * self.temperature_delta
        return math.exp(max(-50.0, min(50.0, exponent)))

    def is_functional(self) -> bool:
        """True when the supply is inside the functional window."""
        return self._powered and self.vdd >= self.vdd_min_effective

    # ----------------------------------------------------------------- state
    @abstractmethod
    def reset(self) -> None:
        """Return to the post-power-on state."""

    def set_inputs(self, values: Mapping[str, int | None]) -> None:
        """Drive input pins (``None`` = keep previous value)."""
        for pin, value in values.items():
            spec = self.pins.get(pin)
            if spec is None or not spec.is_input:
                continue
            if value is None:
                continue
            self._inputs[pin] = 1 if value else 0
        self._apply_input_defects()

    def _apply_input_defects(self) -> None:
        """Stuck-at / open / short effects on the input side."""
        for pin, value in self._input_stuck.items():
            if pin in self._inputs:
                self._inputs[pin] = value
        for pin in self._opens:
            if pin in self._inputs:
                self._inputs[pin] = 0
        for left, right in self._shorts:
            if left in self._inputs and right in self._inputs:
                merged = self._inputs[left] & self._inputs[right]
                self._inputs[left] = self._inputs[right] = merged

    def drive(self, pin: str, value: int) -> None:
        """Drive one input pin."""
        self.set_inputs({pin: value})

    def outputs(self) -> dict[str, int | None]:
        """Current output state (``None`` = floating / unknown)."""
        if not self.is_functional():
            return dict.fromkeys(self._out)
        return dict(self._out)

    def evaluate(self, values: Mapping[str, int | None] | None = None) -> dict[str, int | None]:
        """Drive inputs and return the settled combinational outputs."""
        if values:
            self.set_inputs(values)
        if not self.is_functional():
            return self.outputs()
        self._eval_comb()
        self._post_process()
        return self.outputs()

    def clock_cycle(self, values: Mapping[str, int | None] | None = None) -> dict[str, int | None]:
        """Apply one active clock edge and return the new outputs."""
        if values:
            self.set_inputs(values)
        if not self.is_functional():
            return self.outputs()
        self._clock_cycles += 1
        self._clock_edge()
        self._apply_timing_metastability()
        self._post_process()
        return self.outputs()

    def _apply_timing_metastability(self) -> None:
        """Optional random bit flips (defect model), never used by default."""
        if self._bitflip <= 0:
            return
        for pin in self._out:
            if self._out[pin] is None:
                continue
            if self.rng.random() < self._bitflip:
                self._out[pin] = 0 if self._out[pin] else 1

    def _post_process(self) -> None:
        """Apply output-side defects."""
        for pin in self._opens:
            if pin in self._out:
                self._out[pin] = None
        for pin, value in self._output_stuck.items():
            if pin in self._out:
                self._out[pin] = value
        for left, right in self._shorts:
            if left in self._out and right in self._out:
                merged: int | None = 1 if (self._out[left] or self._out[right]) else 0
                if self._out[left] is None or self._out[right] is None:
                    merged = None
                self._out[left] = self._out[right] = merged

    # -------------------------------------------------------------- analog IO
    def pin_leakage(self, pin: str, volts: float) -> float:
        """Input leakage current in ampere at ``volts`` (positive into pin)."""
        spec = self.check_pin(pin)
        if spec.direction == "power":
            return 0.0
        base = float(self.params["leakage_na"]) * 1e-9 * self.leak_scale
        ratio = 1.0 if self.vdd_nom == 0 else max(0.0, min(2.0, volts / self.vdd_nom))
        current = base * (0.3 + ratio)
        if pin in self._opens:
            current = base * 0.02
        current += self._defect_leak.get(pin, 0.0)
        for left, right in self._shorts:
            if left == pin or right == pin:
                current *= 1.0  # shorts show up in continuity, not leakage
        return current * self._thermal_scale("leak_tempco_per_c")

    def continuity_vdiode(self, pin: str, polarity: int = 1) -> float:
        """Protection-diode forward drop in volt for a small forced current.

        ``polarity=+1`` pushes current into the pin (diode to VSS forward
        biased, ~0.68 V); ``polarity=-1`` sinks current (the upper diode
        conducts, ~-0.68 V).  An open pin floats to the rail, a short reads
        almost 0 V.
        """
        self.check_pin(pin)
        sign = 1.0 if polarity >= 0 else -1.0
        if pin in self._opens:
            rail = self.vdd_nom if self._powered else 3.3
            return sign * (rail + 0.05)
        for left, right in self._shorts:
            if left == pin or right == pin:
                return sign * 0.02
        return sign * max(0.0, self.diode_v + float(self.params["diode_tempco_v_per_c"]) * self.temperature_delta)

    def supply_current(self, vdd: float | None = None, *, freq_mhz: float = 0.0,
                       activity: float = 0.5) -> float:
        """Total supply current in ampere at ``vdd`` for a given activity."""
        vdd = self._vdd if vdd is None else vdd
        if vdd <= 0:
            return 0.0
        static = float(self.params["idd_static_ua"]) * 1e-6
        static *= self.idd_scale * self._high_idd * (vdd / self.vdd_nom) ** 2
        leak = sum(self._defect_leak.values()) * 0.5
        cap = float(self.params["idd_dynamic_nf"]) * 1e-9
        dynamic = cap * vdd * (freq_mhz * 1e6) * max(0.0, activity)
        dynamic *= self.idd_scale * self._high_idd
        total = static * self._thermal_scale("idd_tempco_per_c") + dynamic + leak * self._thermal_scale("leak_tempco_per_c")
        if not self._powered:
            return 0.0
        return max(0.0, total)

    # ---------------------------------------------------------------- timing
    def min_period_ns(self, vdd: float | None = None) -> float:
        """Smallest clock period the DUT can run at ``vdd`` (nanoseconds)."""
        vdd = self._vdd if vdd is None else vdd
        base = float(self.params["base_period_ns"]) * self.slow_derate * self._thermal_scale("period_tempco_per_c")
        vth = float(self.params["vth_v"]) + self.vth_shift
        if vdd <= vth + 0.05:
            return math.inf
        headroom = self.vdd_nom - vth
        return base * max(0.35, headroom / (vdd - vth))

    def max_freq_mhz(self, vdd: float | None = None) -> float:
        """Highest clock frequency in MHz at ``vdd``."""
        period = self.min_period_ns(vdd)
        return 0.0 if math.isinf(period) else 1000.0 / period

    def output_voltage(self, pin: str, current_a: float = 0.0,
                       rout_ohm: float = 50.0) -> float | None:
        """Output voltage under a load current (used by VOH/VOL tests)."""
        spec = self.check_pin(pin)
        if not spec.is_output:
            raise DUTError(f"pin '{pin}' is not an output")
        value = self.outputs().get(pin)
        if value is None:
            return None
        drop = abs(current_a) * rout_ohm
        volts = (self.vdd - 0.1 - drop) if value else (0.05 + drop)
        return max(0.0, min(self.vdd, volts))

    # --------------------------------------------------------------- defects
    def inject(self, defect: Defect) -> None:
        """Inject one defect (idempotent per defect instance)."""
        self.defects.append(defect)
        kind = defect.kind
        if kind == "stuck_at":
            if defect.pin is None:
                raise DUTError("stuck_at requires a pin")
            self.check_pin(defect.pin)
            value = 0 if defect.value is None else int(defect.value) & 1
            spec = self.pins[defect.pin]
            if spec.is_input:
                self._input_stuck[defect.pin] = value
            if spec.is_output:
                self._output_stuck[defect.pin] = value
        elif kind == "open":
            if defect.pin is None:
                raise DUTError("open requires a pin")
            self.check_pin(defect.pin)
            self._opens.add(defect.pin)
        elif kind == "short":
            if not defect.pin or not defect.pin2:
                raise DUTError("short requires pin and pin2")
            self.check_pin(defect.pin)
            self.check_pin(defect.pin2)
            self._shorts.append((defect.pin, defect.pin2))
        elif kind == "leakage":
            if defect.pin is None:
                raise DUTError("leakage requires a pin")
            self.check_pin(defect.pin)
            self._defect_leak[defect.pin] = defect.na * 1e-9
        elif kind == "high_idd":
            self._high_idd *= max(0.1, defect.factor)
        elif kind == "slow":
            self.slow_derate *= max(0.1, defect.factor)
        elif kind == "random_bitflip":
            self._bitflip = max(self._bitflip, defect.prob)
        else:
            raise DUTError(f"unknown defect kind '{kind}'")

    def inject_all(self, defects: Iterable[Defect]) -> None:
        """Inject several defects."""
        for defect in defects:
            self.inject(defect)

    def apply_process_variation(self, *, vth_shift: float = 0.0, idd_scale: float = 1.0,
                                leak_scale: float = 1.0) -> None:
        """Apply die-to-die process variation."""
        self.vth_shift = vth_shift
        self.idd_scale = idd_scale
        self.leak_scale = leak_scale
        self.diode_v = float(self.params["diode_v"]) + self.rng.uniform(-0.02, 0.02)

    def defect_summary(self) -> list[str]:
        """Descriptions of every injected defect."""
        return [defect.describe() for defect in self.defects]
