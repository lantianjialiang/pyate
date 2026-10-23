"""Result analytics: bin pareto, capability (Cpk) and histograms."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

__all__ = ["Capability", "capability", "bin_pareto", "histogram", "CpkAccumulator"]


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
            "cp": round(self.cp, 4),
            "cpk": round(self.cpk, 4),
        }


class CpkAccumulator:
    """Collects values per test number to compute capability at the end."""

    def __init__(self) -> None:
        self._values: dict[int, list[float]] = {}
        self._meta: dict[int, tuple[str, float | None, float | None]] = {}

    def add(self, test_num: int, value: float | None, name: str = "",
            lo: float | None = None, hi: float | None = None) -> None:
        """Record one measurement."""
        if value is None:
            return
        self._values.setdefault(test_num, []).append(float(value))
        self._meta[test_num] = (name, lo, hi)

    def report(self) -> list[Capability]:
        """Compute capability for every collected test."""
        out: list[Capability] = []
        for test_num, values in sorted(self._values.items()):
            name, lo, hi = self._meta.get(test_num, ("", None, None))
            out.append(capability(test_num, values, lo=lo, hi=hi, name=name))
        return out


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
