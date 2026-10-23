"""FlowExecutor: runs a test flow for one site / one part."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ..config.models import FlowNode
from ..hal.base import HALBackend, HALError
from ..testitems.base import LimitsTable, TestItem
from ..testitems.registry import create_testitem
from .binning import BinningEngine
from .context import PartResult, RunContext, SiteContext, TestResult
from .events import EventBus, EventKind
from .flow import ExpressionError, SafeExpression

__all__ = ["HAL_FAIL_CODE", "RunAborted", "FlowExecutor"]

_LOG = logging.getLogger("pyate.executor")
HAL_FAIL_CODE = 99


class RunAborted(RuntimeError):
    """Raised when the user aborts a run."""


class FlowExecutor:
    """Executes a test program flow for a single site.

    The executor holds no global state, so one instance per site can run in
    parallel threads without locking.
    """

    def __init__(
        self,
        *,
        program_nodes: list[FlowNode],
        stage: str,
        limits: LimitsTable,
        binning: BinningEngine,
        hal: HALBackend,
        events: EventBus | None = None,
        temperature: float = 25.0,
        pattern_loader: Callable[[str], Any] | None = None,
        only: list[str] | None = None,
        skip: list[str] | None = None,
        overhead_ms: float = 0.0,
        barrier: Any = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        breakpoints: set[int] | None = None,
        step_generation: Callable[[], int] | None = None,
        hooks: Any = None,
        on_part_start: Callable[[SiteContext], None] | None = None,
        on_item_done: Callable[[SiteContext, TestItem, list[TestResult]], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.nodes = program_nodes
        self.stage = stage
        self.limits = limits
        self.binning = binning
        self.hal = hal
        self.events = events or EventBus()
        self.temperature = temperature
        self.pattern_loader = pattern_loader
        self.only = set(only or [])
        self.skip = set(skip or [])
        self.overhead_ms = overhead_ms
        self.barrier = barrier
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self.breakpoints = breakpoints if breakpoints is not None else set()
        self.step_generation = step_generation
        self._seen_step = 0
        self.hooks = hooks
        self.on_part_start = on_part_start
        self.on_item_done = on_item_done
        self._log = logger or _LOG

    # ------------------------------------------------------------------ run
    def run_part(self, ctx: SiteContext, run_ctx: RunContext | None = None) -> PartResult:
        """Execute the flow on ``ctx`` and return the finished part."""
        ctx.extra["pattern_loader"] = self.pattern_loader
        if self.step_generation is not None:
            self._seen_step = int(self.step_generation())
        start_time = self.hal.elapsed_ms(ctx.site_id)
        forced_bin: int | None = None
        self._barrier_active = self.barrier is not None
        self._publish(EventKind.PART_START, site=ctx.site_id, part_id=ctx.part_id,
                      x=ctx.x_coord, y=ctx.y_coord)
        if self.on_part_start is not None:
            self.on_part_start(ctx)
        self._call_hook("before_part", ctx=ctx, run_ctx=run_ctx)
        if ctx.extra.get("contact_fail"):
            self._record_contact_fail(ctx)
            forced_bin = 2
        try:
            if forced_bin is None:
                forced_bin = self._run_nodes(self.nodes, ctx)
        finally:
            self._leave_barrier()
        sbin, hbin = self.binning.compute(ctx, forced_bin)
        elapsed = self.hal.elapsed_ms(ctx.site_id) - start_time + self.overhead_ms
        ctx.test_time_ms = elapsed
        executed = [r for r in ctx.results if not r.skipped]
        part = PartResult(
            part_id=ctx.part_id,
            site=ctx.site_id,
            x=ctx.x_coord,
            y=ctx.y_coord,
            soft_bin=sbin,
            hard_bin=hbin,
            passed=self.binning.is_pass(sbin),
            fail_code=ctx.fail_code,
            num_tests=len(executed),
            test_time_ms=elapsed,
            retest=ctx.retest,
            skipped=ctx.skipped,
            results=list(ctx.results),
            extra={"stage": self.stage, "temperature": self.temperature},
        )
        payload = part.to_dict()
        first_fail = next((row for row in ctx.results if not row.passed and not row.skipped), None)
        if first_fail is not None:
            payload["first_fail_name"] = first_fail.test_name
            payload["first_fail_value"] = first_fail.value
        self._publish(EventKind.PART_END, part=payload, site=ctx.site_id,
                      soft_bin=sbin, hard_bin=hbin, passed=part.passed)
        self._call_hook("after_part", ctx=ctx, part=part)
        return part

    # --------------------------------------------------------------- helpers
    def _run_nodes(self, nodes: list[FlowNode], ctx: SiteContext) -> int | None:
        for node in nodes:
            if ctx.stopped:
                return ctx.vars.get("forced_bin")
            self._check_abort()
            self._wait_if_paused()
            if not node.enabled:
                continue
            kind = node.kind()
            if kind == "item":
                forced = self._run_item(node, ctx)
                if forced is not None:
                    ctx.vars["forced_bin"] = forced
                    return forced
            elif kind == "group":
                forced = self._run_nodes(node.items, ctx)
                if forced is not None:
                    return forced
            elif kind == "branch":
                assert node.branch is not None
                try:
                    take_then = bool(SafeExpression(node.branch.if_).evaluate(ctx))
                except ExpressionError as exc:
                    self._publish(EventKind.ERROR, site=ctx.site_id, error=str(exc))
                    take_then = False
                forced = self._run_nodes(node.branch.then if take_then else node.branch.else_, ctx)
                if forced is not None:
                    return forced
            elif kind == "loop":
                assert node.loop is not None
                for _ in range(max(0, node.loop.count)):
                    if ctx.stopped:
                        break
                    forced = self._run_nodes(node.loop.items, ctx)
                    if forced is not None:
                        return forced
            elif kind == "goto_bin":
                ctx.stopped = True
                ctx.vars["forced_bin"] = int(node.goto_bin or 0)
                return int(node.goto_bin or 0)
        return ctx.vars.get("forced_bin")

    def _run_item(self, node: FlowNode, ctx: SiteContext) -> int | None:
        name = str(node.item)
        if not node.enabled:
            return None
        if self.only and name not in self.only:
            return None
        if name in self.skip:
            return None
        test_num = int(node.test_num or 0)
        if ctx.stopped:
            self._add_skipped(ctx, name, test_num)
            return None
        stepping = self._consume_step()
        if test_num in set(self.breakpoints):
            self._publish(EventKind.LOG, message=f"breakpoint hit: test {test_num} {name}",
                          site=ctx.site_id, test_num=test_num)
            self.pause_event.set()
            self._wait_if_paused()
            if self._consume_step():
                stepping = True
        if self._barrier_active:
            self.barrier.wait()
            self._barrier_active = self.barrier is not None and not getattr(
                self.barrier, "aborted", False)

        self._publish(EventKind.TEST_START, site=ctx.site_id, item=name, test_num=test_num)
        item: TestItem | None = None
        results: list[TestResult] = []
        try:
            item = create_testitem(name, test_num, node.params)
            self._call_hook("before_item", ctx=ctx, item=item)
            item.setup(ctx, self.hal)
            results = list(item.execute(ctx, self.hal))
        except RunAborted:
            raise
        except HALError as exc:
            result = TestResult(
                test_num=test_num, test_name=name, site=ctx.site_id, value=None,
                passed=False, fail_code=HAL_FAIL_CODE, extra={"error": str(exc)},
            )
            results = [result]
            self._publish(EventKind.ERROR, site=ctx.site_id, item=name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - a broken item must not kill the run
            self._log.exception("item %s crashed", name)
            result = TestResult(
                test_num=test_num, test_name=name, site=ctx.site_id, value=None,
                passed=False, fail_code=HAL_FAIL_CODE, extra={"error": repr(exc)},
            )
            results = [result]
            self._publish(EventKind.ERROR, site=ctx.site_id, item=name, error=repr(exc))
        finally:
            if item is not None:
                try:
                    item.cleanup(ctx, self.hal)
                except Exception:  # noqa: BLE001
                    self._log.warning("cleanup failed for %s", name, exc_info=True)

        if not results:
            results = [TestResult(test_num=test_num, test_name=name, site=ctx.site_id,
                                  value=None, passed=True, extra={"force_pass": True})]
        for result in results:
            self._apply_limits(result)
            ctx.add_result(result)
            self._publish(EventKind.TEST_RESULT, result=result.to_dict(), site=ctx.site_id,
                          item=name, passed=result.passed)
        if self.on_item_done is not None:
            self.on_item_done(ctx, item, results)  # type: ignore[arg-type]
        self._call_hook("after_item", ctx=ctx, item=item, results=results)
        self._publish(EventKind.FLOW_STEP, site=ctx.site_id, item=name,
                      test_num=test_num, passed=all(r.passed for r in results))
        if stepping:
            self.pause_event.set()
            self._publish(EventKind.LOG, message=f"paused after step: test {test_num} {name}",
                          site=ctx.site_id, test_num=test_num)

        failed = [r for r in results if not r.passed and not r.skipped]
        if not failed:
            return None
        self._call_hook("on_fail", ctx=ctx, item=item, results=failed)
        action = node.on_fail.action if node.on_fail else "continue"
        bin_override = node.on_fail.bin if node.on_fail else None
        if action == "stop":
            ctx.stopped = True
            self._leave_barrier()
            if bin_override is not None:
                return int(bin_override)
            return None
        if bin_override is not None:
            ctx.vars.setdefault("forced_bin", int(bin_override))
        return None

    def _add_skipped(self, ctx: SiteContext, name: str, test_num: int) -> None:
        ctx.results.append(
            TestResult(test_num=test_num, test_name=name, site=ctx.site_id,
                       value=None, passed=True, skipped=True)
        )

    def _record_contact_fail(self, ctx: SiteContext) -> None:
        """Probe contact failure: fail continuity and stop the part."""
        result = TestResult(
            test_num=1000, test_name="continuity_contact", site=ctx.site_id,
            value=None, passed=False, fail_code=2,
            extra={"contact_fail": True, "force_pass": False},
        )
        self._apply_limits(result)
        result.passed = False
        result.fail_code = 2
        ctx.add_result(result)
        ctx.stopped = True
        self._publish(EventKind.TEST_RESULT, result=result.to_dict(), site=ctx.site_id,
                      item="continuity", passed=False)

    def _apply_limits(self, result: TestResult) -> None:
        if result.kind == "FTR":
            return
        limit = self.limits.lookup(result.test_num, self.stage, self.temperature)
        if limit is not None:
            result.judge(limit.lo, limit.hi)
            if limit.unit:
                result.unit = limit.unit
        else:
            result.judge(result.lo_limit, result.hi_limit)

    def _publish(self, kind: EventKind, **data: Any) -> None:
        self.events.publish(kind, data)

    def _check_abort(self) -> None:
        if self.stop_event.is_set():
            raise RunAborted("run aborted by user")

    def _consume_step(self) -> bool:
        """True once per ``step()`` request, shared by every site executor."""
        if self.step_generation is None:
            return False
        generation = int(self.step_generation())
        if generation == self._seen_step:
            return False
        self._seen_step = generation
        return True

    def _wait_if_paused(self) -> None:
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise RunAborted("run aborted while paused")
            time.sleep(0.02)

    def _leave_barrier(self) -> None:
        if getattr(self, "_barrier_active", False) and self.barrier is not None:
            self._barrier_active = False
            try:
                self.barrier.leave()
            except Exception:  # pragma: no cover - defensive
                pass

    def _call_hook(self, name: str, **kwargs: Any) -> None:
        if self.hooks is None:
            return
        func = getattr(self.hooks, name, None)
        if func is None:
            return
        try:
            func(**kwargs)
        except Exception:  # noqa: BLE001 - plugin failures are isolated
            self._log.warning("plugin hook %s failed", name, exc_info=True)
