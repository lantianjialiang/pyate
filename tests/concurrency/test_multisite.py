"""Concurrency tests: multi-site scheduling, barriers and per-site isolation."""

from __future__ import annotations

import threading
import time

import pytest

from pyate.config.loader import load_session
from pyate.dut.registry import create_dut
from pyate.engine.binning import BinningEngine
from pyate.engine.context import SiteContext
from pyate.engine.events import EventBus
from pyate.engine.executor import FlowExecutor
from pyate.engine.site import SiteBarrier, SiteScheduler, estimate_uph
from pyate.hal.simulated import SimulatedHAL
from pyate.pattern.parser import load_pattern
from pyate.testitems.base import LimitsTable

from conftest import CONFIGS, CP_SESSION

FLOW = [
    {"item": "continuity", "test_num": 1000, "params": {"pins": ["RSTN", "EN"]}},
    {"item": "idd_static", "test_num": 2000},
    {"item": "functional", "test_num": 3000, "params": {"pattern": "cnt_basic.pat"}},
    {"item": "delay_ms", "test_num": 4000, "params": {"ms": 0.2}},
]


def _session():
    return load_session(CP_SESSION, {"run.sites": 4, "run.seed": 42, "lot.wafers": ["W01"],
                                     "wafer.diameter_mm": 150})


def _build(session, site_count: int):
    hal = SimulatedHAL.from_session(session, site_count=site_count)
    nodes = [type(session.program.flow("CP1")[0]) .model_validate(node) for node in FLOW]
    contexts = []
    for site in range(1, site_count + 1):
        dut = create_dut(session.dut_profile, seed=7, x=site, y=1, sample_yield=False)
        hal.bind_dut(site, dut)
        hal.site(site).dps.set_voltage("VDD", 3.3)
        hal.site(site).dps.connect("VDD")
        ctx = SiteContext(site_id=site, dut=dut, part_id=f"P{site}")
        contexts.append(ctx)
    return hal, nodes, contexts


def _scheduler(session, hal, nodes, mode: str, events=None) -> SiteScheduler:
    def factory(barrier):
        if barrier is None:
            return FlowExecutor(
                program_nodes=nodes, stage="CP1",
                limits=LimitsTable(session.limits),
                binning=BinningEngine(session.binning), hal=hal,
                events=events or EventBus(),
                pattern_loader=lambda name: load_pattern(
                    CONFIGS / "demo_cp" / "patterns" / name),
            )
        return FlowExecutor(
            program_nodes=nodes, stage="CP1",
            limits=LimitsTable(session.limits),
            binning=BinningEngine(session.binning), hal=hal,
            events=events or EventBus(), barrier=barrier,
            pattern_loader=lambda name: load_pattern(
                CONFIGS / "demo_cp" / "patterns" / name),
        )

    return SiteScheduler(factory, mode=mode, events=events or EventBus())


@pytest.mark.parametrize("mode", ["sequential", "thread"])
def test_scheduler_runs_every_site(mode) -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 4)
    parts = _scheduler(session, hal, nodes, mode).run_touchdown(contexts)
    assert len(parts) == 4
    assert {part.site for part in parts} == {1, 2, 3, 4}
    assert all(part.num_tests > 0 for part in parts)


def test_thread_and_serial_modes_agree() -> None:
    session = _session()
    hal_serial, nodes, contexts = _build(session, 4)
    serial = _scheduler(session, hal_serial, nodes, "sequential").run_touchdown(contexts)
    hal_thread, _nodes, thread_contexts = _build(session, 4)
    threaded = _scheduler(session, hal_thread, nodes, "thread").run_touchdown(thread_contexts)
    assert [p.soft_bin for p in serial] == [p.soft_bin for p in threaded]
    assert [p.num_tests for p in serial] == [p.num_tests for p in threaded]


def test_unknown_mode_is_rejected() -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 1)
    with pytest.raises(ValueError):
        _scheduler(session, hal, nodes, "quantum")


def test_each_site_keeps_its_own_results() -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 4)
    _scheduler(session, hal, nodes, "thread").run_touchdown(contexts)
    for ctx in contexts:
        assert ctx.results, ctx.site_id
        assert {result.site for result in ctx.results} == {ctx.site_id}
        assert ctx.dut is hal.dut(ctx.site_id)


def test_events_are_ordered_per_site() -> None:
    session = _session()
    bus = EventBus()
    recorded: list[str] = []
    bus.subscribe("*", lambda event: recorded.append(event.kind))
    hal, nodes, contexts = _build(session, 3)
    _scheduler(session, hal, nodes, "thread", bus).run_touchdown(contexts)
    assert recorded.count("PART_START") == 3
    assert recorded.count("PART_END") == 3


def test_skipped_site_produces_a_skipped_part() -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 2)
    contexts[1].skipped = True
    parts = _scheduler(session, hal, nodes, "sequential").run_touchdown(contexts)
    assert len(parts) == 2
    assert [part.skipped for part in parts] == [False, True]


# ------------------------------------------------------------------- barrier
def test_barrier_releases_every_party() -> None:
    barrier = SiteBarrier(3)
    seen: list[float] = []
    started = time.monotonic()

    def worker() -> None:
        barrier.wait(timeout=5.0)
        seen.append(time.monotonic() - started)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    assert len(seen) == 3
    assert not barrier.aborted


def test_barrier_abort_wakes_waiters() -> None:
    barrier = SiteBarrier(2)
    released: list[bool] = []

    def worker() -> None:
        barrier.wait(timeout=5.0)
        released.append(True)

    thread = threading.Thread(target=worker)
    thread.start()
    barrier.abort()
    thread.join(timeout=5.0)
    assert barrier.aborted
    assert released == [True]


def test_barrier_leave_removes_a_party() -> None:
    barrier = SiteBarrier(2)
    barrier.leave()
    barrier.wait(timeout=1.0)  # must not block: only one party left
    assert not barrier.aborted


class _SpyBarrier:
    """Wraps the scheduler's barrier to count synchronisation points."""

    def __init__(self, inner: SiteBarrier) -> None:
        self.inner = inner
        self.waits = 0
        self._lock = threading.Lock()

    @property
    def aborted(self) -> bool:
        return self.inner.aborted

    def wait(self, timeout: float = 120.0) -> None:
        with self._lock:
            self.waits += 1
        self.inner.wait(timeout=timeout)

    def leave(self) -> None:
        self.inner.leave()

    def abort(self) -> None:
        self.inner.abort()


def test_scheduler_with_barrier_keeps_sites_in_step() -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 4)
    spies: list[_SpyBarrier] = []
    spies_lock = threading.Lock()

    def factory(barrier: SiteBarrier | None) -> FlowExecutor:
        assert barrier is not None, "thread mode must synchronise sites item by item"
        spy = _SpyBarrier(barrier)
        with spies_lock:
            spies.append(spy)
        return FlowExecutor(
            program_nodes=nodes, stage="CP1",
            limits=LimitsTable(session.limits),
            binning=BinningEngine(session.binning), hal=hal,
            events=EventBus(), barrier=spy,
            pattern_loader=lambda name: load_pattern(
                CONFIGS / "demo_cp" / "patterns" / name),
        )

    parts = SiteScheduler(factory, mode="thread").run_touchdown(contexts)
    assert len(parts) == 4
    assert len(spies) == 4
    # every site shares the very same barrier instance
    assert len({id(spy.inner) for spy in spies}) == 1
    # each site waited at least once per shared flow item
    assert all(spy.waits >= 1 for spy in spies)
    assert sum(spy.waits for spy in spies) >= 4 * len(nodes)
    # the scheduler aborts the barrier once the touchdown is done
    assert spies[0].inner.aborted


def test_sequential_mode_does_not_synchronise() -> None:
    session = _session()
    hal, nodes, contexts = _build(session, 4)
    seen: list[SiteBarrier | None] = []

    def factory(barrier: SiteBarrier | None) -> FlowExecutor:
        seen.append(barrier)
        return FlowExecutor(
            program_nodes=nodes, stage="CP1",
            limits=LimitsTable(session.limits),
            binning=BinningEngine(session.binning), hal=hal,
            events=EventBus(), barrier=barrier,
            pattern_loader=lambda name: load_pattern(
                CONFIGS / "demo_cp" / "patterns" / name),
        )

    SiteScheduler(factory, mode="sequential").run_touchdown(contexts)
    assert seen == [None, None, None, None]


# ------------------------------------------------------------------- throughput
def test_estimate_uph_scales_with_sites() -> None:
    one = estimate_uph(1, 100.0)
    four = estimate_uph(4, 100.0)
    assert four == pytest.approx(one * 4)


def test_estimate_uph_accounts_for_index_time() -> None:
    with_index = estimate_uph(4, 100.0, 50.0)
    without = estimate_uph(4, 100.0)
    assert with_index < without
