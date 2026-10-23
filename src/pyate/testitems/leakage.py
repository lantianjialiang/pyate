"""Input leakage tests at VIL and VIH."""

from __future__ import annotations

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["LeakageParams", "LeakageItem"]


class LeakageParams(TestItemParams):
    """Leakage parameters (limits are for ``abs(current)``)."""

    pins: list[str] = []
    vil: float | None = None
    vih: float | None = None
    irange: float = 10e-6
    lo: float | None = None
    hi: float | None = 250e-9
    fail_code: int = 3


@register_testitem("leakage_ih_il")
class LeakageItem(TestItem):
    """Measures input leakage with the PMU in FVMI mode."""

    name = "leakage_ih_il"
    Params = LeakageParams
    description = "Input leakage at VIL and VIH"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("leakage test needs a bound DUT", site=ctx.site_id)
        levels = resources.digital.levels
        vil = self.params.vil if self.params.vil is not None else levels.vil
        vih = self.params.vih if self.params.vih is not None else levels.vih
        pins = list(self.params.pins) or list(dut.input_pins)
        lo, hi = self.default_limits()
        results: list[TestResult] = []
        for index, pin in enumerate(pins):
            for offset, (label, volts) in enumerate((("il", vil), ("ih", vih))):
                try:
                    resources.pmu.force_voltage(pin, volts, self.params.irange)
                    current = abs(resources.pmu.measure_current(pin))
                finally:
                    resources.pmu.disconnect(pin)
                results.append(
                    self.make_result(
                        ctx,
                        test_num=self.test_num + index * 2 + offset,
                        name=f"leakage_{label}_{pin}",
                        value=current,
                        unit="A",
                        lo=lo,
                        hi=hi,
                        pin=pin,
                    )
                )
        return results
