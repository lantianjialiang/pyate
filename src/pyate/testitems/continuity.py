"""Continuity test: forward-bias the ESD diode of every pin."""

from __future__ import annotations

from typing import Any

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["ContinuityParams", "ContinuityItem"]


class ContinuityParams(TestItemParams):
    """Continuity parameters."""

    pins: list[str] = []
    current_a: float = -100e-6
    vclamp: float = 3.6
    lo: float | None = -1.2
    hi: float | None = -0.2
    fail_code: int = 2
    power_pin: str = "VDD"


@register_testitem("continuity")
class ContinuityItem(TestItem):
    """Forces a small current into each pin and measures the diode drop."""

    name = "continuity"
    Params = ContinuityParams
    description = "Per-pin ESD diode continuity"

    def setup(self, ctx: SiteContext, hal: HALBackend) -> None:
        resources = hal.site(ctx.site_id)
        self._restore_vdd = resources.dps.is_connected(self.params.power_pin)
        self._vdd = 0.0
        if self._restore_vdd:
            self._vdd = getattr(resources.dps, "_voltage", {}).get(self.params.power_pin, 3.3)
            resources.dps.disconnect(self.params.power_pin)

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("continuity needs a bound DUT", site=ctx.site_id)
        pins = list(self.params.pins) or list(dut.signal_pins)
        results: list[TestResult] = []
        lo, hi = self.default_limits()
        for index, pin in enumerate(pins):
            try:
                resources.pmu.force_current(pin, self.params.current_a, self.params.vclamp)
                volts = resources.pmu.measure_voltage(pin)
            finally:
                resources.pmu.disconnect(pin)
            result = self.make_result(
                ctx,
                test_num=self.test_num + index,
                name=f"continuity_{pin}",
                value=volts,
                unit="V",
                lo=lo,
                hi=hi,
                pin=pin,
            )
            results.append(result)
        return results

    def cleanup(self, ctx: SiteContext, hal: HALBackend) -> None:
        if getattr(self, "_restore_vdd", False):
            resources = hal.site(ctx.site_id)
            resources.dps.set_voltage(self.params.power_pin, self._vdd)
            resources.dps.connect(self.params.power_pin)

    def _unused(self) -> Any:  # pragma: no cover
        return None
