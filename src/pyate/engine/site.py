"""Multi-site scheduling: parallel touchdowns with item level synchronisation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..plugins.loader import PluginAbort
from .context import PartResult, RunContext, SiteContext
from .events import EventBus, EventKind
from .executor import FlowExecutor

__all__ = ["SiteBarrier", "SiteScheduler", "ThroughputStats", "estimate_uph"]

_LOG = logging.getLogger("pyate.site")

_MODES = frozenset({"thread", "sequential", "async", "process"})


class SiteBarrier:
    """A barrier whose party count shrinks when a site stops early.

    Unlike :class:`threading.Barrier` this never deadlocks when one site
    fail-stops and stops participating.
    """

    def __init__(self, parties: int) -> None:
        self._parties = max(1, int(parties))
        self._waiting = 0
        self._generation = 0
        self._aborted = False
        self._timed_out = False
        self._cond = threading.Condition()

    @property
    def aborted(self) -> bool:
        """True when the barrier was aborted."""
        return self._aborted

    def wait(self, timeout: float = 120.0) -> None:
        """Block until every active site arrives (or the barrier is aborted)."""
        with self._cond:
            if self._timed_out:
                raise TimeoutError("site synchronization timed out")
            if self._aborted or self._parties <= 1:
                return
            generation = self._generation
            self._waiting += 1
            if self._waiting >= self._parties:
                self._generation += 1
                self._waiting = 0
                self._cond.notify_all()
                return
            while generation == self._generation and not self._aborted:
                if not self._cond.wait(timeout):
                    self._timed_out = True
                    self._aborted = True
                    self._generation += 1
                    self._waiting = 0
                    self._cond.notify_all()
                    raise TimeoutError("site synchronization timed out")
            if self._timed_out:
                raise TimeoutError("site synchronization timed out")

    def leave(self) -> None:
        """Remove one site from the barrier (called on fail-stop/finish)."""
        with self._cond:
            if self._parties > 0:
                self._parties -= 1
            if self._parties <= 0 or self._waiting >= self._parties:
                self._generation += 1
                self._waiting = 0
            self._cond.notify_all()

    def abort(self) -> None:
        """Release everyone permanently."""
        with self._cond:
            self._aborted = True
            self._generation += 1
            self._cond.notify_all()


@dataclass
class ThroughputStats:
    """Test time / UPH accounting for one touchdown."""

    site_count: int
    touchdown_ms: float
    index_time_ms: float

    @property
    def total_ms(self) -> float:
        """Index plus test time for one touchdown."""
        return self.touchdown_ms + self.index_time_ms

    @property
    def uph(self) -> float:
        """Units per hour for this touchdown cadence."""
        if self.total_ms <= 0:
            return 0.0
        return 3600.0 * 1000.0 * self.site_count / self.total_ms

    @property
    def site_seconds(self) -> float:
        """Tester seconds consumed per part."""
        if self.site_count <= 0:
            return 0.0
        return self.total_ms / 1000.0 / self.site_count


def estimate_uph(site_count: int, touchdown_ms: float, index_time_ms: float = 0.0) -> float:
    """Convenience wrapper around :class:`ThroughputStats`."""
    return ThroughputStats(site_count, touchdown_ms, index_time_ms).uph


class SiteScheduler:
    """Runs one touchdown (1..N DUTs) using threads, sequentially or async."""

    def __init__(
        self,
        executor_factory: Callable[[SiteBarrier | None], FlowExecutor],
        *,
        mode: str = "thread",
        max_workers: int | None = None,
        events: EventBus | None = None,
        sync_items: bool = True,
    ) -> None:
        if mode not in _MODES:
            raise ValueError(f"unknown parallel mode {mode!r}, expected one of {sorted(_MODES)}")
        if mode == "process":
            raise ValueError("site parallel_mode=process is not implemented; use thread or sequential")
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.executor_factory = executor_factory
        self.mode = mode
        self.max_workers = max_workers
        self.events = events or EventBus()
        self.sync_items = sync_items
        self._pool: ThreadPoolExecutor | None = None
        self._pool_workers = 0

    def close(self) -> None:
        """Join the reusable site workers after the last touchdown."""
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
            self._pool_workers = 0

    def run_touchdown(self, contexts: Sequence[SiteContext],
                      run_ctx: RunContext | None = None) -> list[PartResult]:
        """Test every context of one touchdown and return the part results."""
        active = [ctx for ctx in contexts if not ctx.skipped]
        skipped = [ctx for ctx in contexts if ctx.skipped]
        results: list[PartResult] = []
        if not active:
            return [self._skipped_part(ctx) for ctx in skipped]
        if self.mode == "sequential" or len(active) == 1:
            for ctx in active:
                executor = self.executor_factory(None)
                results.append(self._run_one(executor, ctx, run_ctx))
        else:
            barrier: SiteBarrier | None = None
            if self.sync_items and self.mode == "thread":
                barrier = SiteBarrier(len(active))
            workers = self.max_workers or len(active)
            if barrier is not None and workers < len(active):
                raise ValueError("synchronized sites require at least one worker per active site")
            if self._pool is None or workers > self._pool_workers:
                self.close()
                self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pyate-site")
                self._pool_workers = workers
            pool = self._pool
            try:
                futures = []
                for ctx in active:
                    executor = self.executor_factory(barrier)
                    futures.append(pool.submit(self._run_one, executor, ctx, run_ctx))
                for future in futures:
                    results.append(future.result())
            finally:
                if barrier is not None:
                    barrier.abort()
        results.extend(self._skipped_part(ctx) for ctx in skipped)
        results.sort(key=lambda part: part.site)
        return results

    def _run_one(self, executor: FlowExecutor, ctx: SiteContext,
                 run_ctx: RunContext | None) -> PartResult:
        try:
            return executor.run_part(ctx, run_ctx)
        except PluginAbort:
            raise
        except Exception as exc:
            _LOG.exception("site %s crashed", ctx.site_id)
            self.events.publish(EventKind.ERROR, {"site": ctx.site_id, "error": repr(exc)})
            return PartResult(
                part_id=ctx.part_id, site=ctx.site_id, x=ctx.x_coord, y=ctx.y_coord,
                soft_bin=0, hard_bin=0, passed=False, fail_code=99,
                test_time_ms=0.0, results=list(ctx.results),
                extra={"error": repr(exc)},
            )
        finally:
            executor._leave_barrier()

    @staticmethod
    def _skipped_part(ctx: SiteContext) -> PartResult:
        return PartResult(
            part_id=ctx.part_id, site=ctx.site_id, x=ctx.x_coord, y=ctx.y_coord,
            soft_bin=0, hard_bin=0, passed=False, skipped=True,
        )
