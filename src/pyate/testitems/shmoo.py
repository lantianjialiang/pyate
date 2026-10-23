"""Shmoo plot: two dimensional VDD x period pass/fail scan."""

from __future__ import annotations

from typing import Any

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem
from .vdd_sweep import _load_pattern, _run, _set_period, _set_supply

__all__ = ["ShmooParams", "ShmooItem"]


class ShmooParams(TestItemParams):
    """Shmoo scan parameters."""

    pattern: str = "cnt_loop.pat"
    vdd_min: float = 2.7
    vdd_max: float = 3.6
    vdd_steps: int = 6
    period_min_ns: float = 10.0
    period_max_ns: float = 120.0
    period_steps: int = 6
    cycles: int = 64
    min_pass_rate: float = 100.0
    fail_code: int = 7


@register_testitem("shmoo")
class ShmooItem(TestItem):
    """Builds a pass/fail matrix and publishes it on the site context."""

    name = "shmoo"
    Params = ShmooParams
    description = "VDD x period shmoo"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        if resources.digital.dut is None:
            raise HALError("shmoo needs a bound DUT", site=ctx.site_id)
        pattern = _load_pattern(ctx, self.params.pattern)
        vdd_axis = _axis(self.params.vdd_min, self.params.vdd_max, self.params.vdd_steps)
        period_axis = _axis(self.params.period_min_ns, self.params.period_max_ns,
                            self.params.period_steps)
        original_timing = resources.digital.timing
        cell_passes = 0
        matrix: list[list[int]] = []
        details: list[dict[str, Any]] = []
        try:
            for vdd in vdd_axis:
                _set_supply(hal, ctx, vdd)
                row: list[int] = []
                for period in period_axis:
                    _set_period(resources.digital, period)
                    ok = _run(resources.digital, pattern, self.params.cycles)
                    row.append(1 if ok else 0)
                    cell_passes += int(ok)
                    details.append({"vdd": vdd, "period_ns": period, "pass": bool(ok)})
                matrix.append(row)
        finally:
            _set_supply(hal, ctx, resources.digital.levels.vdd.nom)
            _set_period(resources.digital, original_timing.period_ns)
        total = max(1, len(vdd_axis) * len(period_axis))
        rate = 100.0 * cell_passes / total
        payload = {
            "vdd_axis": vdd_axis,
            "period_axis": period_axis,
            "matrix": matrix,
            "cells": details,
            "pass_rate": rate,
        }
        ctx.extra["shmoo"] = payload
        result = self.make_result(
            ctx, name="shmoo_pass_rate", value=rate, unit="%",
            lo=self.params.min_pass_rate, hi=None,
            extra={"vdd_steps": len(vdd_axis), "period_steps": len(period_axis)},
        )
        result.extra["shmoo"] = payload
        return [result]


def _axis(start: float, stop: float, steps: int) -> list[float]:
    """Inclusive linspace with rounded values."""
    steps = max(1, int(steps))
    if steps == 1:
        return [round(start, 6)]
    return [round(start + (stop - start) * index / (steps - 1), 6) for index in range(steps)]
