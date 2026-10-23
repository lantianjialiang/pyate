"""Parametric searches: minimum VDD and maximum frequency."""

from __future__ import annotations

import math
from typing import Any

from ..config.models import TimingSet
from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from ..pattern.model import Pattern
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["VddMinParams", "VddMinSearchItem", "FmaxParams", "FmaxSearchItem"]


def _load_pattern(ctx: SiteContext, name: str) -> Pattern:
    loader = ctx.extra.get("pattern_loader")
    if loader is None:
        raise HALError("search item requires a pattern loader", site=ctx.site_id)
    return loader(name)


def _set_supply(hal: HALBackend, ctx: SiteContext, volts: float) -> None:
    resources = hal.site(ctx.site_id)
    resources.digital.set_vdd(volts)
    for pin in ("VDD", "VCC"):
        if resources.dps.is_connected(pin):
            resources.dps.set_voltage(pin, volts)


def _set_period(channel: Any, period_ns: float) -> TimingSet:
    base = channel.timing
    override = TimingSet(name=base.name, period_ns=period_ns, edges=base.edges)
    if hasattr(channel, "override_timings"):
        channel.override_timings(override)
    else:  # pragma: no cover - third party HAL
        channel.apply_timing(override)
    return override


def _run(channel: Any, pattern: Pattern, cycles: int) -> bool:
    channel.load_pattern(pattern)
    return bool(channel.run(stop_on_first_fail=True, max_cycles=cycles).passed)


class VddMinParams(TestItemParams):
    """VDD min search parameters."""

    pattern: str = "cnt_loop.pat"
    vdd_start: float = 3.6
    vdd_stop: float = 2.0
    resolution: float = 0.01
    cycles: int = 256
    restore_vdd: float | None = None
    lo: float | None = None
    hi: float | None = None
    fail_code: int = 7


@register_testitem("vdd_min_search")
class VddMinSearchItem(TestItem):
    """Binary search for the lowest functional supply voltage."""

    name = "vdd_min_search"
    Params = VddMinParams
    description = "Minimum operating VDD"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        pattern = _load_pattern(ctx, self.params.pattern)
        channel = resources.digital
        high = max(self.params.vdd_start, self.params.vdd_stop)
        low = min(self.params.vdd_start, self.params.vdd_stop)
        restore = self.params.restore_vdd or resources.digital.levels.vdd.nom
        vmin: float
        try:
            _set_supply(hal, ctx, high)
            if not _run(channel, pattern, self.params.cycles):
                vmin = high
            else:
                while high - low > self.params.resolution:
                    mid = round((high + low) / 2.0, 6)
                    _set_supply(hal, ctx, mid)
                    if _run(channel, pattern, self.params.cycles):
                        high = mid
                    else:
                        low = mid
                vmin = high
        finally:
            _set_supply(hal, ctx, restore)
        lo, hi = self.default_limits()
        result = self.make_result(ctx, name="vdd_min", value=vmin, unit="V", lo=lo, hi=hi,
                                  extra={"pattern": pattern.name,
                                         "vdd_start": self.params.vdd_start})
        if vmin >= max(self.params.vdd_start, self.params.vdd_stop):
            result.fail(self.params.fail_code)
            result.extra["reason"] = "no functional point at start voltage"
        return [result]


class FmaxParams(TestItemParams):
    """Fmax search parameters."""

    pattern: str = "cnt_loop.pat"
    period_min_ns: float = 2.0
    period_max_ns: float = 200.0
    resolution_ns: float = 0.5
    cycles: int = 256
    restore_period_ns: float | None = None
    lo: float | None = None
    hi: float | None = None
    fail_code: int = 7


@register_testitem("fmax_search")
class FmaxSearchItem(TestItem):
    """Binary search for the shortest passing period (highest frequency)."""

    name = "fmax_search"
    Params = FmaxParams
    description = "Maximum operating frequency"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        pattern = _load_pattern(ctx, self.params.pattern)
        channel = resources.digital
        fast = min(self.params.period_min_ns, self.params.period_max_ns)
        slow = max(self.params.period_min_ns, self.params.period_max_ns)
        original = getattr(channel, "timing", None)
        restore_period = self.params.restore_period_ns or (
            original.period_ns if original is not None else slow)
        passed_at_slow = True
        try:
            _set_period(channel, slow)
            passed_at_slow = _run(channel, pattern, self.params.cycles)
            best = slow
            if passed_at_slow:
                while slow - fast > self.params.resolution_ns:
                    mid = round((slow + fast) / 2.0, 6)
                    _set_period(channel, mid)
                    if _run(channel, pattern, self.params.cycles):
                        slow = mid
                        best = mid
                    else:
                        fast = mid
        finally:
            _set_period(channel, restore_period)
        fmax = 0.0 if not passed_at_slow else 1000.0 / best
        lo, hi = self.default_limits()
        result = self.make_result(
            ctx, name="fmax", value=fmax, unit="MHz", lo=lo, hi=hi,
            extra={"period_ns": best, "pattern": pattern.name},
        )
        if not passed_at_slow:
            result.fail(self.params.fail_code)
            result.extra["reason"] = "functional fail at the slowest period"
        if not math.isfinite(fmax):
            result.fail(self.params.fail_code)
        return [result]
