"""DC tests: IDD static/dynamic, VOH/VOL, power-up sequence."""

from __future__ import annotations

import math

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = [
    "IddStaticParams", "IddStaticItem",
    "IddDynamicParams", "IddDynamicItem",
    "VolVohParams", "VolVohItem",
    "PowerUpDownParams", "PowerUpDownItem",
]


class _SupplyItem(TestItem):
    """Shared helper: make sure the supply is connected and programmed."""

    def _ensure_supply(self, ctx: SiteContext, hal: HALBackend, pin: str, volts: float) -> object:
        resources = hal.site(ctx.site_id)
        if not resources.dps.is_connected(pin):
            resources.dps.set_voltage(pin, volts)
            resources.dps.connect(pin)
        else:
            resources.dps.set_voltage(pin, volts)
        return resources


class IddStaticParams(TestItemParams):
    """Static IDD parameters."""

    vdd: float | None = None
    power_pin: str = "VDD"
    settle_ms: float = 1.0
    lo: float | None = None
    hi: float | None = 260e-6
    fail_code: int = 4


@register_testitem("idd_static")
class IddStaticItem(_SupplyItem):
    """Quiescent supply current with all inputs static."""

    name = "idd_static"
    Params = IddStaticParams
    description = "Static IDD"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        levels = resources.digital.levels
        vdd = self.params.vdd if self.params.vdd is not None else levels.vdd.nom
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("IDD test needs a bound DUT", site=ctx.site_id)
        self._ensure_supply(ctx, hal, self.params.power_pin, vdd)
        for pin in dut.input_pins:
            dut.drive(pin, 0)
        dut.evaluate()
        hal.add_time(ctx.site_id, self.params.settle_ms)
        current = resources.dps.measure_current(self.params.power_pin)
        lo, hi = self.default_limits()
        return [
            self.make_result(ctx, name="idd_static", value=current, unit="A", lo=lo, hi=hi,
                             extra={"vdd": vdd})
        ]


class IddDynamicParams(TestItemParams):
    """Dynamic IDD parameters."""

    vdd: float | None = None
    power_pin: str = "VDD"
    freq_mhz: float = 10.0
    activity: float = 0.5
    pattern: str | None = None
    cycles: int = 64
    lo: float | None = None
    hi: float | None = 5e-3
    fail_code: int = 4


@register_testitem("idd_dynamic")
class IddDynamicItem(_SupplyItem):
    """Supply current while the DUT is switching at ``freq_mhz``."""

    name = "idd_dynamic"
    Params = IddDynamicParams
    description = "Dynamic IDD"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        levels = resources.digital.levels
        vdd = self.params.vdd if self.params.vdd is not None else levels.vdd.nom
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("IDD test needs a bound DUT", site=ctx.site_id)
        self._ensure_supply(ctx, hal, self.params.power_pin, vdd)
        if self.params.pattern:
            loader = ctx.extra.get("pattern_loader")
            pattern = loader(self.params.pattern) if loader else None
            if pattern is not None:
                resources.digital.load_pattern(pattern)
                resources.digital.run(stop_on_first_fail=False, max_cycles=self.params.cycles)
        hal.add_time(ctx.site_id, 0.5)
        current = dut.supply_current(vdd, freq_mhz=self.params.freq_mhz,
                                    activity=self.params.activity)
        current = abs(self.noise(hal, ctx.site_id, current, 0.01))
        lo, hi = self.default_limits()
        return [
            self.make_result(ctx, name="idd_dynamic", value=current, unit="A", lo=lo, hi=hi,
                             extra={"vdd": vdd, "freq_mhz": self.params.freq_mhz})
        ]


class VolVohParams(TestItemParams):
    """Output level parameters."""

    pins: list[str] = []
    iol: float = 4e-3
    ioh: float = -4e-3
    vol_lo: float | None = -0.1
    vol_hi: float | None = 0.4
    voh_lo: float | None = 2.4
    voh_hi: float | None = 3.6
    cycles: int = 3
    fail_code: int = 5


@register_testitem("vol_voh")
class VolVohItem(TestItem):
    """Measures VOL for low outputs and VOH for high outputs."""

    name = "vol_voh"
    Params = VolVohParams
    description = "Output VOL / VOH"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("VOL/VOH needs a bound DUT", site=ctx.site_id)
        if not dut.is_functional():
            resources.digital.set_vdd(resources.digital.levels.vdd.nom)
        # toggle a few cycles so the outputs are not all identical
        for _ in range(max(1, self.params.cycles)):
            dut.clock_cycle({"RSTN": 1, "EN": 1})
        outputs = dut.outputs()
        pins = list(self.params.pins) or [pin for pin in dut.output_pins if pin != "SO"]
        results: list[TestResult] = []
        for index, pin in enumerate(pins):
            level = outputs.get(pin)
            if level is None:
                results.append(
                    self.make_result(
                        ctx, test_num=self.test_num + index * 2,
                        name=f"vol_{pin}", value=None, unit="V",
                        lo=self.params.vol_lo, hi=self.params.vol_hi, pin=pin,
                    )
                )
                continue
            current = self.params.ioh if level else self.params.iol
            volts = dut.output_voltage(pin, current)
            volts = self.noise(hal, ctx.site_id, volts or 0.0, 0.005)
            test_num = self.test_num + index * 2 + (1 if level else 0)
            name = f"voh_{pin}" if level else f"vol_{pin}"
            lo = self.params.voh_lo if level else self.params.vol_lo
            hi = self.params.voh_hi if level else self.params.vol_hi
            results.append(
                self.make_result(ctx, test_num=test_num, name=name, value=volts, unit="V",
                                 lo=lo, hi=hi, pin=pin,
                                 extra={"load_a": current, "logic": level})
            )
        return results


class PowerUpDownParams(TestItemParams):
    """Power sequencing parameters."""

    vdd: float | None = None
    steps: int = 8
    step_ms: float = 0.5
    power_pin: str = "VDD"
    lo: float | None = 0.0
    hi: float | None = 20.0
    fail_code: int = 5


@register_testitem("power_up_down")
class PowerUpDownItem(_SupplyItem):
    """Ramps the supply up, checks the current and the final state."""

    name = "power_up_down"
    Params = PowerUpDownParams
    description = "Power-up / power-down sequence"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        resources = hal.site(ctx.site_id)
        levels = resources.digital.levels
        vdd = self.params.vdd if self.params.vdd is not None else levels.vdd.nom
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None:
            raise HALError("power sequence needs a bound DUT", site=ctx.site_id)
        steps = max(1, self.params.steps)
        elapsed = 0.0
        peak = 0.0
        for step in range(steps + 1):
            volts = vdd * step / steps
            resources.dps.set_voltage(self.params.power_pin, volts)
            if not resources.dps.is_connected(self.params.power_pin):
                resources.dps.connect(self.params.power_pin)
            hal.add_time(ctx.site_id, self.params.step_ms)
            elapsed += self.params.step_ms
            if volts > 0.5:
                peak = max(peak, dut.supply_current(volts))
        valid = dut.is_functional()
        resources.dps.set_voltage(self.params.power_pin, 0.0)
        resources.dps.disconnect(self.params.power_pin)
        resources.dps.set_voltage(self.params.power_pin, vdd)
        resources.dps.connect(self.params.power_pin)
        if not valid:
            result = self.make_result(ctx, name="power_up_down", value=math.nan, unit="ms",
                                      lo=self.params.lo, hi=self.params.hi)
            result.fail(self.params.fail_code)
            result.extra["reason"] = "DUT not functional after ramp"
            return [result]
        lo, hi = self.default_limits()
        return [
            self.make_result(ctx, name="power_up_down", value=elapsed, unit="ms", lo=lo, hi=hi,
                             extra={"peak_current_a": peak})
        ]
