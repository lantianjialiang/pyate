"""Statistical yield / defect sampling, including wafer spatial effects."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from .base import DUTModel, Defect

__all__ = ["YieldSampler", "mix_seed"]


def mix_seed(seed: int, x: float = 0.0, y: float = 0.0) -> int:
    """Deterministic 64-bit mix of a run seed and die coordinates."""
    value = (int(seed) & 0xFFFFFFFFFFFFFFFF) * 1000003
    value ^= (int(round(x * 1000)) & 0xFFFFFFFF) << 17
    value ^= (int(round(y * 1000)) & 0xFFFFFFFF) << 3
    value ^= value >> 29
    return value & 0xFFFFFFFFFFFFFFFF


@dataclass
class YieldSampler:
    """Samples whether a die is defective and which defect it carries.

    Failure probability increases towards the wafer edge and inside randomly
    placed cluster regions, mimicking real wafer maps.
    """

    yield_spec: Any
    seed: int
    wafer_radius: float | None = None
    pin_source: DUTModel | None = None
    cluster_center: tuple[float, float] | None = None
    cluster_radius: float = 9.0
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed ^ 0x5DEECE66D)
        if self.wafer_radius and self._rng.random() < float(
            getattr(self.yield_spec, "cluster_prob", 0.0)
        ):
            angle = self._rng.uniform(0, 2 * math.pi)
            distance = self.wafer_radius * math.sqrt(self._rng.random()) * 0.8
            self.cluster_center = (distance * math.cos(angle), distance * math.sin(angle))

    @property
    def base_fail(self) -> float:
        """Bulk (center) failure probability."""
        return max(0.0, min(1.0, 1.0 - float(self.yield_spec.pass_rate)))

    def fail_probability(self, x: float = 0.0, y: float = 0.0) -> float:
        """Failure probability at die coordinate ``(x, y)`` in millimetres."""
        probability = self.base_fail
        if self.wafer_radius:
            radius = math.hypot(x, y) / self.wafer_radius
            boost = float(getattr(self.yield_spec, "edge_fail_boost", 1.0))
            probability *= 1.0 + (boost - 1.0) * min(1.0, radius) ** 3
        if self.cluster_center is not None:
            distance = math.hypot(x - self.cluster_center[0], y - self.cluster_center[1])
            if distance <= self.cluster_radius:
                probability = min(0.95, probability * 3.0)
        return max(0.0, min(1.0, probability))

    def is_bad(self, x: float = 0.0, y: float = 0.0) -> bool:
        """Draw one bad/good decision for a die."""
        return self._rng.random() < self.fail_probability(x, y)

    def _pick_kind(self) -> str:
        mix: dict[str, float] = dict(getattr(self.yield_spec, "defect_mix", {}) or {})
        if not mix:
            return "stuck_at"
        total = sum(max(0.0, weight) for weight in mix.values())
        if total <= 0:
            return "stuck_at"
        pick = self._rng.random() * total
        for kind, weight in mix.items():
            pick -= max(0.0, weight)
            if pick <= 0:
                return kind
        return next(iter(mix))

    def _signals(self, dut: DUTModel) -> list[str]:
        return [pin for pin in dut.signal_pins if pin != "CLK"]

    def make_defect(self, dut: DUTModel, kind: str | None = None) -> Defect:
        """Build one defect of ``kind`` bound to actual DUT pins."""
        kind = kind or self._pick_kind()
        pins = self._signals(dut)
        outputs = [pin for pin in dut.output_pins if pin != "SO"]
        inputs = [pin for pin in dut.input_pins if pin != "CLK"]
        if kind == "stuck_at":
            pin = self._rng.choice(outputs or pins)
            return Defect("stuck_at", pin=pin, value=self._rng.randint(0, 1))
        if kind == "open":
            pin = self._rng.choice(outputs or pins)
            return Defect("open", pin=pin)
        if kind == "short":
            if len(outputs) >= 2:
                left, right = self._rng.sample(outputs, 2)
            elif len(pins) >= 2:
                left, right = self._rng.sample(pins, 2)
            else:  # pragma: no cover - every model has >=2 pins
                return Defect("leakage", pin=pins[0], na=500.0)
            return Defect("short", pin=left, pin2=right)
        if kind == "leakage":
            pin = self._rng.choice(inputs or pins)
            return Defect("leakage", pin=pin, na=self._rng.uniform(300.0, 3000.0))
        if kind == "high_idd":
            return Defect("high_idd", factor=self._rng.uniform(2.0, 6.0))
        if kind == "slow":
            return Defect("slow", factor=self._rng.uniform(1.6, 4.0))
        if kind == "random_bitflip":
            return Defect("random_bitflip", prob=self._rng.uniform(0.01, 0.08))
        return Defect("stuck_at", pin=self._rng.choice(outputs or pins), value=0)

    def sample(self, dut: DUTModel, x: float = 0.0, y: float = 0.0) -> list[Defect]:
        """Return zero or more defects for the die at ``(x, y)``."""
        if not self.is_bad(x, y):
            return []
        kind = self._pick_kind()
        defects = [self.make_defect(dut, kind)]
        # 15% of the bad dies carry a second, independent defect
        if self._rng.random() < 0.15:
            defects.append(self.make_defect(dut))
        return defects

    def defects_for(self, x: float, y: float, dut: DUTModel | None = None) -> list[Defect]:
        """Convenience wrapper used by the factory."""
        target = dut or self.pin_source
        if target is None:
            raise ValueError("YieldSampler needs a DUT instance to bind defects to pins")
        return self.sample(target, x, y)
