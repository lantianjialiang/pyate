"""Linearity test items shipped with the ADC reference plugin."""

from __future__ import annotations

from typing import Any

from pyate.engine.context import SiteContext, TestResult
from pyate.hal.base import HALBackend, HALError
from pyate.testitems.base import TestItem, TestItemParams
from pyate.testitems.registry import register_testitem

__all__ = ["AdcInlDnlParams", "AdcInlDnlItem", "AdcOffsetGainParams", "AdcOffsetGainItem"]


class AdcInlDnlParams(TestItemParams):
    """Ramp parameters for the INL/DNL measurement."""

    vref: float | None = None
    codes: int = 0
    lo_dnl: float = -1.0
    hi_dnl: float = 1.0
    lo_inl: float = -1.5
    hi_inl: float = 1.5
    fail_code: int = 5


@register_testitem("adc_inl_dnl")
class AdcInlDnlItem(TestItem):
    """Measures DNL and INL over a full input ramp.

    The part is clocked like a real conversion (``START`` pulse + ``conv_clocks``
    edges) and the digital bus is read back, so a broken DUT (stuck code, dead
    output) shows up as a huge DNL instead of a silently perfect transfer curve.
    """

    name = "adc_inl_dnl"
    Params = AdcInlDnlParams
    description = "ADC integral / differential non-linearity"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None or not hasattr(dut, "convert"):
            raise HALError("adc_inl_dnl requires an ADC DUT model", site=ctx.site_id)
        bits = int(getattr(dut, "bits", 8))
        vref = float(self.params.vref or getattr(dut, "vref", 3.3))
        n_codes = int(self.params.codes) or (1 << bits)
        mask = n_codes - 1
        resources = hal.site(ctx.site_id)
        digital = resources.digital

        # 8 hits per code keeps the histogram quantisation well below 1 LSB
        samples = max(32, n_codes * 8)
        histogram = [0] * n_codes
        for step in range(samples):
            volts = vref * (step + 0.5) / samples
            code = min(max(int(self._convert(dut, digital, volts)), 0), mask)
            histogram[code] += 1

        ideal = samples / n_codes
        dnl_max = 0.0
        inl_max = 0.0
        cumulative = 0.0
        for code, hits in enumerate(histogram):
            dnl = (hits / ideal) - 1.0 if ideal else 0.0
            cumulative += dnl
            dnl_max = max(dnl_max, abs(dnl))
            inl_max = max(inl_max, abs(cumulative))

        dnl_result = self.make_result(
            ctx, test_num=self.test_num, name="adc_dnl_max", value=round(dnl_max, 4),
            unit="LSB", lo=self.params.lo_dnl, hi=self.params.hi_dnl,
        )
        inl_result = self.make_result(
            ctx, test_num=self.test_num + 1, name="adc_inl_max", value=round(inl_max, 4),
            unit="LSB", lo=self.params.lo_inl, hi=self.params.hi_inl,
        )
        for result in (dnl_result, inl_result):
            result.judge(result.lo_limit, result.hi_limit)
        ctx.extra["adc_dnl_max"] = dnl_max
        ctx.extra["adc_inl_max"] = inl_max
        ctx.extra["adc_histogram"] = histogram
        return [dnl_result, inl_result]

    @staticmethod
    def _convert(dut: Any, digital: Any, volts: float) -> int:
        """Clock one conversion through the digital channel and read the bus."""
        dut.set_vin(volts)
        dut.set_inputs({"START": 1, "CLK": 0})
        dut.clock_cycle()
        dut.set_inputs({"START": 0})
        for _ in range(int(getattr(dut, "conv_clocks", dut.bits)) + 1):
            dut.clock_cycle()
        if digital is not None and hasattr(digital, "measure_outputs"):
            outputs = digital.measure_outputs()
        else:
            outputs = dut.outputs()
        code = 0
        for index in range(int(getattr(dut, "bits", 8))):
            if outputs.get(f"D{index}"):
                code |= 1 << index
        return code


class AdcOffsetGainParams(TestItemParams):
    """Offset / gain limits in LSB."""

    lo_offset: float = -8.0
    hi_offset: float = 8.0
    lo_gain: float = 0.97
    hi_gain: float = 1.03
    fail_code: int = 5


@register_testitem("adc_offset_gain")
class AdcOffsetGainItem(TestItem):
    """Two-point offset/gain check (0.1 Vref and 0.9 Vref)."""

    name = "adc_offset_gain"
    Params = AdcOffsetGainParams
    description = "ADC offset and gain error"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None or not hasattr(dut, "convert"):
            raise HALError("adc_offset_gain requires an ADC DUT model", site=ctx.site_id)
        bits = int(getattr(dut, "bits", 8))
        vref = float(getattr(dut, "vref", 3.3))
        mask = (1 << bits) - 1
        low = int(dut.convert(0.1 * vref))
        high = int(dut.convert(0.9 * vref))
        expected_low = 0.1 * mask
        expected_high = 0.9 * mask
        offset = low - expected_low
        gain = (high - low) / max(1.0, (expected_high - expected_low))
        offset_result = self.make_result(
            ctx, test_num=self.test_num, name="adc_offset", value=round(offset, 3),
            unit="LSB", lo=self.params.lo_offset, hi=self.params.hi_offset,
        )
        gain_result = self.make_result(
            ctx, test_num=self.test_num + 1, name="adc_gain", value=round(gain, 5),
            unit="", lo=self.params.lo_gain, hi=self.params.hi_gain,
        )
        for result in (offset_result, gain_result):
            result.judge(result.lo_limit, result.hi_limit)
        return [offset_result, gain_result]
