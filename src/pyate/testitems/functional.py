"""Functional test: run a pattern and report failing cycles/pins."""

from __future__ import annotations

from ..engine.context import SiteContext, TestResult
from ..hal.base import HALError, HALBackend
from ..pattern.model import Pattern
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["FunctionalParams", "FunctionalItem"]


class FunctionalParams(TestItemParams):
    """Functional parameters."""

    pattern: str
    start_label: str | None = None
    max_cycles: int | None = None
    stop_on_first_fail: bool = True
    fail_code: int = 6


@register_testitem("functional")
class FunctionalItem(TestItem):
    """Executes a pattern on the digital channel and maps the result to FTR."""

    name = "functional"
    Params = FunctionalParams
    description = "Pattern based functional test"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        loader = ctx.extra.get("pattern_loader")
        if loader is None:
            raise HALError("functional test requires a pattern loader in the run context",
                           site=ctx.site_id)
        try:
            pattern: Pattern = loader(self.params.pattern)
        except Exception as exc:  # noqa: BLE001 - surface as a test failure
            result = self.make_result(ctx, name=f"functional_{self.params.pattern}",
                                      value=None, kind="FTR")
            result.fail(self.params.fail_code)
            result.extra["error"] = f"pattern load failed: {exc}"
            return [result]
        resources = hal.site(ctx.site_id)
        digital = resources.digital
        level_config = getattr(hal, "level_config", None)
        if pattern.levels and level_config is not None:
            try:
                digital.apply_levels(level_config.get(pattern.levels))
            except KeyError:
                pass
        digital.load_pattern(pattern)
        run = digital.run(
            start_label=self.params.start_label,
            stop_on_first_fail=self.params.stop_on_first_fail,
            max_cycles=self.params.max_cycles,
        )
        first = run.fail_log[0] if run.fail_log else None
        result = self.make_result(
            ctx,
            name=f"functional_{pattern.name}",
            value=None,
            unit="",
            kind="FTR",
            vector_name=pattern.name,
            cycle_cnt=run.cycles,
            rel_vadr=first.vector_index if first else 0,
            num_fail=run.fail_count,
            fail_pins=run.fail_pins,
            pattern=pattern.name,
        )
        result.passed = run.passed
        result.test_time_ms = run.elapsed_ms
        if not run.passed:
            result.fail_code = self.params.fail_code
            if first is not None:
                result.extra["first_fail_cycle"] = first.cycle
                result.extra["first_fail_line"] = first.line
                result.extra["expected"] = first.expected
                result.extra["actual"] = {k: v for k, v in first.actual.items()}
        return [result]
