"""Result analytics: bin pareto, capability (Cpk) and histograms."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["Capability", "CpkAccumulator", "bin_pareto", "capability", "histogram"]


@dataclass
class Capability:
    """Process capability of one measured parameter."""

    test_num: int
    name: str = ""
    count: int = 0
    mean: float = 0.0
    sigma: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    stage: str = ""
    temperature: float | None = None
    revision: str = ""

    @property
    def cp(self) -> float:
        """Potential capability (spread vs limits)."""
        if self.sigma <= 0 or self.lo is None or self.hi is None:
            return 0.0
        return (self.hi - self.lo) / (6.0 * self.sigma)

    @property
    def cpk(self) -> float:
        """Actual capability (nearest limit)."""
        if self.sigma <= 0:
            return 0.0
        candidates = []
        if self.lo is not None:
            candidates.append((self.mean - self.lo) / (3.0 * self.sigma))
        if self.hi is not None:
            candidates.append((self.hi - self.mean) / (3.0 * self.sigma))
        return min(candidates) if candidates else 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON friendly view."""
        return {
            "test_num": self.test_num,
            "name": self.name,
            "count": self.count,
            "mean": self.mean,
            "sigma": self.sigma,
            "min": self.minimum,
            "max": self.maximum,
            "lo": self.lo,
            "hi": self.hi,
            "unit": self.unit, "stage": self.stage,
            "temperature": self.temperature, "revision": self.revision,
            "cp": round(self.cp, 4) if self.count > 1 and self.sigma > 0 and self.lo is not None and self.hi is not None else None,
            "cpk": round(self.cpk, 4) if self.count > 1 and self.sigma > 0 and (self.lo is not None or self.hi is not None) else None,
        }


class CpkAccumulator:
    """Welford moments; storage grows with comparable groups, never sample count."""

    def __init__(self) -> None:
        self._groups: dict[tuple[int, str, float | None, float | None, str, str, float | None, str], tuple[Capability, float]] = {}

    def add(self, test_num: int, value: float | None, name: str = "",
            lo: float | None = None, hi: float | None = None, *, unit: str = "",
            stage: str = "", temperature: float | None = None, revision: str = "") -> None:
        """Record one measurement."""
        if value is None or not math.isfinite(value):
            return
        key = (test_num, name, lo, hi, unit, stage, temperature, revision)
        if key not in self._groups:
            self._groups[key] = (Capability(test_num, name=name, lo=lo, hi=hi,
                                            minimum=value, maximum=value, unit=unit,
                                            stage=stage, temperature=temperature, revision=revision), 0.0)
        moment, m2 = self._groups[key]
        moment.count += 1
        delta = value - moment.mean
        moment.mean += delta / moment.count
        m2 += delta * (value - moment.mean)
        moment.minimum = min(moment.minimum, value)
        moment.maximum = max(moment.maximum, value)
        moment.sigma = math.sqrt(max(0.0, m2) / (moment.count - 1)) if moment.count > 1 else 0.0
        self._groups[key] = moment, m2

    def report(self) -> list[Capability]:
        """Compute capability for every collected test."""
        from dataclasses import replace
        return [replace(moment) for _, (moment, _m2) in sorted(self._groups.items(), key=lambda item: repr(item[0]))]


def capability(test_num: int, values: Iterable[float], *,
               lo: float | None = None, hi: float | None = None,
               name: str = "") -> Capability:
    """Compute mean / sigma / Cpk for a value list (sample sigma, n-1)."""
    data = [float(value) for value in values]
    if not data:
        return Capability(test_num=test_num, name=name, lo=lo, hi=hi)
    mean = sum(data) / len(data)
    variance = sum((value - mean) ** 2 for value in data) / max(1, len(data) - 1)
    return Capability(
        test_num=test_num, name=name, count=len(data), mean=mean,
        sigma=math.sqrt(variance), minimum=min(data), maximum=max(data), lo=lo, hi=hi,
    )


def bin_pareto(bin_counts: dict[int, int]) -> list[dict[str, float]]:
    """Bin counts sorted by frequency with percentage share."""
    total = sum(bin_counts.values()) or 1
    rows = [
        {"bin": int(bin), "count": int(count), "pct": round(100.0 * count / total, 2)}
        for bin, count in sorted(bin_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return rows


def histogram(values: Iterable[float], bins: int = 10) -> list[tuple[float, float, int]]:
    """Simple ``(low, high, count)`` histogram."""
    data = sorted(float(value) for value in values)
    if not data:
        return []
    low, high = data[0], data[-1]
    if high == low:
        return [(low, high, len(data))]
    width = (high - low) / bins
    counts = [0] * bins
    for value in data:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return [(low + index * width, low + (index + 1) * width, count)
            for index, count in enumerate(counts)]
