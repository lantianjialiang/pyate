"""SRAM March C- test item (direct array access)."""

from __future__ import annotations

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["SramMarchParams", "SramMarchItem"]


class SramMarchParams(TestItemParams):
    """March test parameters."""

    algorithm: str = "march_c_minus"
    fail_code: int = 8


@register_testitem("sram_march")
class SramMarchItem(TestItem):
    """Runs March C- over the DUT's SRAM array."""

    name = "sram_march"
    Params = SramMarchParams
    description = "March C- memory test"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        dut = ctx.dut or hal.dut(ctx.site_id)  # type: ignore[attr-defined]
        if dut is None or not hasattr(dut, "sram_write"):
            result = self.make_result(ctx, name="sram_march", value=None, kind="FTR")
            result.fail(self.params.fail_code)
            result.extra["error"] = "DUT has no SRAM model"
            return [result]
        if self.params.algorithm != "march_c_minus":
            result = self.make_result(ctx, name="sram_march", value=None, kind="FTR")
            result.fail(self.params.fail_code)
            result.extra["error"] = f"unsupported algorithm '{self.params.algorithm}'"
            return [result]

        depth = int(getattr(dut, "depth", 0))
        mask = int(getattr(dut, "data_mask", 0xFF))
        fail_address: int | None = None
        fail_expect = 0
        fail_got = 0
        operations = 0

        def write(address: int, data: int) -> None:
            nonlocal operations
            dut.sram_write(address, data & mask)
            operations += 1

        def check(address: int, expected: int) -> bool:
            nonlocal operations, fail_address, fail_expect, fail_got
            got = dut.sram_read(address)
            operations += 1
            if got != (expected & mask) and fail_address is None:
                fail_address = address
                fail_expect = expected & mask
                fail_got = got
            return got == (expected & mask)

        # March C-: {⇕(w0); ⇑(r0,w1); ⇑(r1,w0); ⇓(r0,w1); ⇓(r1,w0); ⇕(r0)}
        for address in range(depth):
            write(address, 0)
        for address in range(depth):
            check(address, 0)
            write(address, mask)
        for address in range(depth):
            check(address, mask)
            write(address, 0)
        for address in range(depth - 1, -1, -1):
            check(address, 0)
            write(address, mask)
        for address in range(depth - 1, -1, -1):
            check(address, mask)
            write(address, 0)
        for address in range(depth):
            check(address, 0)

        result = self.make_result(
            ctx, name="sram_march", value=None, unit="", kind="FTR",
            cycle_cnt=operations, num_fail=0 if fail_address is None else 1,
            fail_pins=() if fail_address is None else (f"A{fail_address}",),
            algorithm=self.params.algorithm,
        )
        result.passed = fail_address is None
        if fail_address is not None:
            result.fail_code = self.params.fail_code
            result.extra.update({
                "fail_address": fail_address,
                "expected": fail_expect,
                "actual": fail_got,
            })
        return [result]
