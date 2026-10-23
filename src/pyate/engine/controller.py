"""RunController: drives lots/units/touchdowns and produces the run summary."""

from __future__ import annotations

import json
import logging
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..config import LoadedSession
from ..config.loader import write_snapshot
from ..dut.registry import YieldSampler, create_dut
from ..hal.base import HALBackend, HALError
from ..hal.simulated import SimulatedHAL
from ..testitems.base import LimitsTable, PatternCache
from .binning import BinningEngine
from .context import PartResult, RunContext, SiteContext, TouchdownInfo
from .events import EventBus, EventKind
from .retest import RetestConfig, merge_attempt, should_retest
from .executor import FlowExecutor
from .site import SiteScheduler, ThroughputStats
from ..scenario.base import UnitPlan

__all__ = ["UnitSummary", "RunSummary", "RunController", "DatalogSink"]

_LOG = logging.getLogger("pyate.controller")


class DatalogSink:
    """Minimal sink interface (all methods optional)."""

    def on_run_start(self, controller: "RunController") -> None:
        """Called before the first unit."""

    def on_unit_start(self, unit: UnitPlan) -> None:
        """Called when a wafer/tray starts."""

    def on_part(self, part: PartResult, unit: UnitPlan) -> None:
        """Called for every finished part."""

    def on_unit_end(self, summary: "UnitSummary") -> None:
        """Called when a wafer/tray finishes."""

    def on_run_end(self, summary: "RunSummary") -> None:
        """Called after the last unit."""

    def close(self) -> None:
        """Flush and release resources."""


@dataclass
class UnitSummary:
    """Yield / bin statistics for one wafer or tray."""

    unit_id: str
    kind: str
    parts: int = 0
    passed: int = 0
    tested: int = 0
    bin_counts: dict[int, int] = field(default_factory=dict)
    test_time_ms: float = 0.0
    index_time_ms: float = 0.0
    change_time_ms: float = 0.0
    uph: float = 0.0
    bins: dict[Any, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def yield_pct(self) -> float:
        """Yield in percent (tested parts only)."""
        return 100.0 * self.passed / self.tested if self.tested else 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view (wafer map excluded)."""
        return {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "parts": self.parts,
            "tested": self.tested,
            "passed": self.passed,
            "yield_pct": round(self.yield_pct, 3),
            "bin_counts": {str(k): v for k, v in sorted(self.bin_counts.items())},
            "test_time_ms": round(self.test_time_ms, 3),
            "index_time_ms": self.index_time_ms,
            "change_time_ms": self.change_time_ms,
            "uph": round(self.uph, 2),
        }


@dataclass
class RunSummary:
    """Whole-run aggregate."""

    lot_id: str
    stage: str
    program: str
    seed: int
    started_at: float
    finished_at: float = 0.0
    units: list[UnitSummary] = field(default_factory=list)
    bin_counts: dict[int, int] = field(default_factory=dict)
    total_parts: int = 0
    total_tested: int = 0
    total_passed: int = 0
    uph: float = 0.0
    aborted: bool = False
    files: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def yield_pct(self) -> float:
        """Overall yield in percent."""
        return 100.0 * self.total_passed / self.total_tested if self.total_tested else 0.0

    @property
    def duration_s(self) -> float:
        """Wall clock duration."""
        return max(0.0, (self.finished_at or self.started_at) - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view used by ``summary.json`` and the CLI."""
        return {
            "lot_id": self.lot_id,
            "stage": self.stage,
            "program": self.program,
            "seed": self.seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "total_parts": self.total_parts,
            "total_tested": self.total_tested,
            "total_passed": self.total_passed,
            "yield_pct": round(self.yield_pct, 3),
            "bin_counts": {str(k): v for k, v in sorted(self.bin_counts.items())},
            "uph": round(self.uph, 2),
            "aborted": self.aborted,
            "files": dict(self.files),
            "units": [unit.to_dict() for unit in self.units],
            "meta": dict(self.meta),
        }

    def write(self, path: str | Path) -> Path:
        """Write ``summary.json``."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target


class RunController:
    """Executes one stage of a lot with multi-site parallelism."""

    def __init__(
        self,
        session: LoadedSession,
        *,
        events: EventBus | None = None,
        site_count: int | None = None,
        scenario: Any = None,
        sinks: Sequence[DatalogSink] | None = None,
        stage: str | None = None,
        seed: int | None = None,
        output_dir: str | Path | None = None,
        hal: HALBackend | None = None,
        retest_bins: Sequence[int] | None = None,
        max_retries: int | None = None,
        resume: bool = True,
        die_filter: set[tuple[str, int, int]] | None = None,
        part_ids: Sequence[str] | None = None,
        retest_keep: str = "last",
    ) -> None:
        self.session = session
        self.events = events or EventBus()
        self.stage = stage or session.stage
        self.seed = int(seed if seed is not None else session.run.seed)
        self.site_count = int(site_count or session.site_count())
        self.sinks: list[DatalogSink] = list(sinks or [])
        self.hal = hal or SimulatedHAL.from_session(session, site_count=self.site_count)
        self.limits = LimitsTable(session.limits)
        self.binning = BinningEngine(session.binning)
        self.patterns = PatternCache(self._pattern_path)
        self.output_dir = Path(output_dir or session.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.retest_bins = set(retest_bins if retest_bins is not None else session.run.retest_bins)
        self.max_retries = int(max_retries if max_retries is not None else session.run.max_retries)
        self.retest_keep = retest_keep
        self.retest = RetestConfig(bins=sorted(int(b) for b in self.retest_bins),
                                   max_retries=self.max_retries, keep=retest_keep)
        self.resume_enabled = resume
        self.resume_state_path = self.output_dir / "resume.json"
        self.die_filter = die_filter
        self.part_ids = list(part_ids) if part_ids is not None else None
        self.scenario = scenario or self._build_scenario()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._step_lock = threading.Lock()
        self._step_gen = 0
        self.breakpoints: set[int] = set()
        self._lock = threading.RLock()
        self._parts_by_unit: dict[str, list[PartResult]] = {}
        self._uph_samples: list[float] = []
        self.summary: RunSummary | None = None
        self._resume_state: dict[str, Any] = {}

    # --------------------------------------------------------------- helpers
    def _pattern_path(self, name: str) -> Any:
        """Resolve a pattern name to an absolute path (used by PatternCache)."""
        from ..config.validators import resolve_pattern_path

        path = resolve_pattern_path(self.session, name)
        if path is None:
            raise FileNotFoundError(f"pattern '{name}' not found")
        return path

    def _build_scenario(self) -> Any:
        stage = self.stage.upper()
        if stage.startswith("CP"):
            from ..scenario.cp import CpScenario

            return CpScenario(self.session, self.site_count,
                              die_filter=self.die_filter, seed=self.seed)
        from ..scenario.ft import FtScenario

        return FtScenario(self.session, self.site_count, part_ids=self.part_ids, seed=self.seed)

    def _executor(self, barrier: Any) -> FlowExecutor:
        return FlowExecutor(
            program_nodes=self.session.program.flow(self.stage),
            stage=self.stage,
            limits=self.limits,
            binning=self.binning,
            hal=self.hal,
            events=self.events,
            temperature=self.session.temperature(),
            pattern_loader=self.patterns.get,
            only=self.session.run.only,
            skip=self.session.run.skip,
            overhead_ms=self.session.tester.overhead_ms,
            barrier=barrier,
            stop_event=self._stop,
            pause_event=self._pause,
            breakpoints=self.breakpoints,
            step_generation=self.step_generation,
        )

    # ------------------------------------------------------------- lifecycle
    def pause(self) -> None:
        """Pause before the next test item."""
        self._pause.set()
        self.events.publish(EventKind.LOG, {"message": "run paused"})

    def resume(self) -> None:
        """Resume a paused run."""
        self._pause.clear()
        self.events.publish(EventKind.LOG, {"message": "run resumed"})

    def step(self) -> None:
        """Run one more test item, then pause again."""
        with self._step_lock:
            self._step_gen += 1
        self._pause.clear()
        self.events.publish(EventKind.LOG, {"message": "step"})

    def step_generation(self) -> int:
        """Monotonic counter bumped by :meth:`step`."""
        with self._step_lock:
            return self._step_gen

    def set_breakpoint(self, test_num: int) -> None:
        """Pause before ``test_num`` on every site."""
        self.breakpoints.add(int(test_num))

    def clear_breakpoint(self, test_num: int | None = None) -> None:
        """Drop one breakpoint, or all of them when ``test_num`` is omitted."""
        if test_num is None:
            self.breakpoints.clear()
            return
        self.breakpoints.discard(int(test_num))

    @property
    def paused(self) -> bool:
        """True while the run is paused."""
        return self._pause.is_set()

    def abort(self) -> None:
        """Abort the run at the next safe point."""
        self._stop.set()
        self._pause.clear()
        self.events.publish(EventKind.LOG, {"message": "run aborted"})

    @property
    def aborted(self) -> bool:
        """True once abort was requested."""
        return self._stop.is_set()

    # ------------------------------------------------------------------- run
    def run(self) -> RunSummary:
        """Execute the stage and return the run summary."""
        self._load_resume_state()
        self.events.publish(EventKind.LOT_START, {
            "lot_id": self.session.lot.lot_id if self.session.lot else self.session.name,
            "stage": self.stage,
        })
        summary = RunSummary(
            lot_id=self.session.lot.lot_id if self.session.lot else self.session.name,
            stage=self.stage,
            program=self.session.program.program,
            seed=self.seed,
            started_at=__import__("time").time(),
        )
        snapshot = write_snapshot(self.session, self.output_dir)
        summary.files["snapshot"] = str(snapshot)
        for sink in self.sinks:
            self._notify(sink, "on_run_start", self)
        scheduler = SiteScheduler(self._executor, mode=self.session.run.parallel_mode,
                                  events=self.events)
        try:
            for unit_index, unit in enumerate(self.scenario.units()):
                if self._stop.is_set():
                    summary.aborted = True
                    break
                if unit_index < int(self._resume_state.get("unit_index", 0)):
                    continue
                unit_summary = self._run_unit(unit, scheduler, unit_index)
                summary.units.append(unit_summary)
                summary.total_parts += unit_summary.parts
                summary.total_tested += unit_summary.tested
                summary.total_passed += unit_summary.passed
                for key, value in unit_summary.bin_counts.items():
                    summary.bin_counts[key] = summary.bin_counts.get(key, 0) + value
                summary.files[f"unit_{unit.unit_id}"] = str(
                    self.output_dir / f"{unit.unit_id}_summary.json"
                )
                self._save_resume_state(unit_index + 1, 0)
        finally:
            summary.finished_at = __import__("time").time()
            summary.uph = self._aggregate_uph(summary)
            self.summary = summary
            summary.meta["retests"] = sum(
                int(unit.meta.get("retests", 0) or 0) for unit in summary.units
            )
            self._write_reports(summary)
            for sink in self.sinks:
                try:
                    self._notify(sink, "on_run_end", summary)
                finally:
                    self._notify(sink, "close")
            self.events.publish(EventKind.LOT_END, summary.to_dict())
            self.hal.reset()
        return summary

    # ------------------------------------------------------------------ unit
    def _run_unit(self, unit: UnitPlan, scheduler: SiteScheduler, unit_index: int) -> UnitSummary:
        unit_summary = UnitSummary(
            unit_id=unit.unit_id, kind=unit.kind,
            index_time_ms=float(unit.meta.get("index_time_ms", 0.0)),
            change_time_ms=unit.change_time_ms,
            meta={k: v for k, v in unit.meta.items() if k not in ("wafer_map", "die_filter")},
        )
        self.events.publish(EventKind.UNIT_START, {"unit": unit.unit_id, "kind": unit.kind})
        self.events.publish(
            EventKind.WAFER_START if unit.kind == "wafer" else EventKind.UNIT_START,
            {"unit": unit.unit_id},
        )
        for sink in self.sinks:
            self._notify(sink, "on_unit_start", unit)
        wafer_map = unit.meta.get("wafer_map")
        bins: dict[Any, int] = {}
        unit_seed = (self.seed ^ zlib.crc32(unit.unit_id.encode("utf-8"))) & 0xFFFFFFFF
        sampler = YieldSampler(
            self.session.dut_profile.yield_,
            unit_seed,
            wafer_radius=wafer_map.radius_mm if wafer_map is not None else None,
        )
        skip_touchdowns = 0
        if unit_index == int(self._resume_state.get("unit_index", 0)):
            skip_touchdowns = int(self._resume_state.get("touchdowns_done", 0))
        touchdowns = list(unit.touchdowns)
        unit_start = __import__("time").time()
        for touch_index, touchdown in enumerate(touchdowns):
            if self._stop.is_set():
                break
            if touch_index < skip_touchdowns:
                continue
            parts = self._run_touchdown(touchdown, unit, sampler, unit_seed)
            touchdown_ms = 0.0
            for part in parts:
                if part.skipped:
                    continue
                unit_summary.parts += 1
                unit_summary.tested += 1
                if part.passed:
                    unit_summary.passed += 1
                unit_summary.bin_counts[part.soft_bin] = (
                    unit_summary.bin_counts.get(part.soft_bin, 0) + 1
                )
                if part.retest:
                    summary_retests = unit_summary.meta.get("retests", 0) + 1
                    unit_summary.meta["retests"] = summary_retests
                touchdown_ms = max(touchdown_ms, part.test_time_ms)
                key: Any = (part.x, part.y) if unit.kind == "wafer" else part.part_id
                bins[key] = part.soft_bin
                self._parts_by_unit.setdefault(unit.unit_id, []).append(part)
                for sink in self.sinks:
                    self._notify(sink, "on_part", part, unit)
            unit_summary.test_time_ms += touchdown_ms
            if touchdown.xtra.get("jam"):
                recovery = float(touchdown.xtra.get("jam_recovery_ms", 0.0))
                self.hal.add_time(1, recovery)
                self.events.publish(EventKind.LOG, {"message": "handler jam recovered",
                                                    "unit": unit.unit_id})
            self._save_resume_state(unit_index, touch_index + 1)
            done = max(1, touch_index + 1 - skip_touchdowns)
            elapsed_ms = (__import__("time").time() - unit_start) * 1000.0
            live_sites = max(1, self.site_count - len(self.session.run.disabled_sites))
            uph = ThroughputStats(
                live_sites,
                elapsed_ms / done,
                float(touchdown.index_time_ms),
            ).uph
            self.events.publish(EventKind.PROGRESS, {
                "unit": unit.unit_id,
                "touchdown": touch_index + 1,
                "touchdowns": len(touchdowns),
                "uph": round(uph, 2),
            })
        elapsed = (__import__("time").time() - unit_start) * 1000.0
        count = max(1, len(touchdowns))
        unit_summary.uph = ThroughputStats(
            self.site_count,
            unit_summary.test_time_ms / count,
            float(unit.meta.get("index_time_ms", 0.0)),
        ).uph
        unit_summary.meta["wall_ms"] = round(elapsed, 3)
        unit_summary.meta["touchdowns"] = len(touchdowns)
        unit_summary.meta["wafer_id"] = unit.unit_id
        if wafer_map is not None:
            unit_summary.bins = {k: v for k, v in bins.items()}
            unit_summary.meta["dies"] = wafer_map.die_count
            unit_summary.meta["wafer_yield_pct"] = round(wafer_map.yield_of(bins), 3)
            unit_summary.meta["wafer_map_text"] = wafer_map.render_text(bins)
            svg_path = self.output_dir / f"{unit.unit_id}_binmap.svg"
            wafer_map.render_svg(bins, svg_path)
            (self.output_dir / f"{unit.unit_id}_binmap.txt").write_text(
                wafer_map.render_text(bins), encoding="utf-8"
            )
            unit_summary.meta["binmap_svg"] = str(svg_path)
            wafer_map.render_csv(bins, self.output_dir / f"{unit.unit_id}_binmap.csv")
        self.events.publish(EventKind.UNIT_END, {
            "unit": unit.unit_id, "tested": unit_summary.tested,
            "passed": unit_summary.passed, "yield_pct": round(unit_summary.yield_pct, 3),
        })
        if unit.kind == "wafer":
            self.events.publish(EventKind.WAFER_END, {"unit": unit.unit_id})
        for sink in self.sinks:
            self._notify(sink, "on_unit_end", unit_summary)
        (self.output_dir / f"{unit.unit_id}_summary.json").write_text(
            json.dumps(unit_summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return unit_summary

    # -------------------------------------------------------------- touchdown
    def _run_touchdown(self, touchdown: TouchdownInfo, unit: UnitPlan,
                       sampler: YieldSampler, unit_seed: int) -> list[PartResult]:
        contexts: list[SiteContext] = []
        active = {site for site, _x, _y, _pid in touchdown.sites}
        die_filter = unit.meta.get("die_filter")
        if die_filter is not None:
            allowed = {
                (unit.unit_id, x, y) for _site, x, y, _pid in touchdown.sites
                if (unit.unit_id, x, y) in die_filter
            }
        else:
            allowed = None
        entries = {site: (x, y, pid) for site, x, y, pid in touchdown.sites}
        disabled = {int(site) for site in self.session.run.disabled_sites}
        masked = set(touchdown.xtra.get("masked_sites", [])) | disabled | (
            {site for site in range(1, self.site_count + 1) if site not in active}
        )
        contact_fail = set(touchdown.xtra.get("contact_fail_sites", set()))
        for site in range(1, self.site_count + 1):
            ctx = SiteContext(site_id=site)
            if site in masked:
                ctx.skipped = True
                ctx.part_id = f"{unit.unit_id}-S{site}-EMPTY"
                contexts.append(ctx)
                continue
            x, y, part_id = entries[site]
            ctx.part_id = part_id
            ctx.x_coord = x
            ctx.y_coord = y
            ctx.touch_id = touchdown.touch_id
            if allowed is not None and (unit.unit_id, x, y) not in allowed:
                ctx.skipped = True
                contexts.append(ctx)
                continue
            dut_seed = unit_seed
            dut = create_dut(
                self.session.dut_profile,
                seed=dut_seed,
                x=x if unit.kind == "wafer" else site,
                y=y if unit.kind == "wafer" else touchdown.touch_id,
                sampler=sampler,
            )
            self.hal.bind_dut(site, dut)
            ctx.dut = dut
            ctx.pin_map = self.hal.pin_map(site) if hasattr(self.hal, "pin_map") else {}
            ctx.extra["contact_fail"] = site in contact_fail
            contexts.append(ctx)
        self.hal.reset_clock()
        self.events.publish(EventKind.TOUCHDOWN, {
            "unit": unit.unit_id,
            "kind": unit.kind,
            "touchdown": touchdown.touch_id,
            "sites": len(active),
            "slots": [
                {"site": int(site), "x": int(x), "y": int(y), "part_id": str(part_id)}
                for site, x, y, part_id in touchdown.sites
            ],
            "tray": touchdown.xtra.get("tray", unit.unit_id),
            "jam": bool(touchdown.xtra.get("jam")),
            "masked_sites": sorted(int(site) for site in masked),
            "contact_fail_sites": sorted(int(site) for site in contact_fail),
            "layout": list(touchdown.xtra.get("layout") or ()),
            "configured_layout": list(touchdown.xtra.get("configured_layout") or ()),
            "scan": touchdown.xtra.get("scan", ""),
        })
        results = self._scheduler_run(contexts)
        results = self._apply_retest(results, contexts, unit, sampler, unit_seed)
        return results

    def _scheduler_run(self, contexts: list[SiteContext]) -> list[PartResult]:
        scheduler = SiteScheduler(self._executor, mode=self.session.run.parallel_mode,
                                  events=self.events)
        return scheduler.run_touchdown(contexts)

    def _apply_retest(self, results: list[PartResult], contexts: list[SiteContext],
                      unit: UnitPlan, sampler: YieldSampler, unit_seed: int) -> list[PartResult]:
        policy = self.retest
        if not policy.enabled:
            return results
        merged = {part.site: part for part in results}
        for attempt in range(1, policy.max_retries + 1):
            retry_sites = [
                part.site for part in merged.values()
                if should_retest(part, policy, attempt)
            ]
            if not retry_sites:
                break
            for site in retry_sites:
                ctx = next(item for item in contexts if item.site_id == site)
                # reset the per-part state so the retest starts from a clean slate
                ctx.new_part(part_id=ctx.part_id, x=ctx.x_coord, y=ctx.y_coord,
                             retest=True)
                ctx.extra["contact_fail"] = False
                # a retest is the *same* physical part: reuse the original seed so
                # the defect set stays identical and only the contact condition
                # changes between attempts.
                dut = create_dut(
                    self.session.dut_profile,
                    seed=unit_seed,
                    x=ctx.x_coord if unit.kind == "wafer" else site,
                    y=ctx.y_coord if unit.kind == "wafer" else ctx.touch_id,
                    sampler=sampler,
                )
                self.hal.bind_dut(site, dut)
                ctx.dut = dut
            retry_contexts = [item for item in contexts if item.site_id in retry_sites]
            retry_parts = self._scheduler_run(retry_contexts)
            for part in retry_parts:
                part.extra["retest_attempt"] = attempt
                previous = merged.get(part.site)
                merged[part.site] = (
                    merge_attempt(previous, part, policy.keep) if previous is not None else part
                )
                if merged[part.site].passed:
                    self.events.publish(EventKind.LOG, {
                        "message": "part recovered on retest",
                        "site": part.site,
                        "part_id": part.part_id,
                        "attempt": attempt,
                    })
        for part in merged.values():
            if part.retest:
                n_prev = len(part.extra.get("previous_attempts", []))
                self.events.publish(EventKind.LOG, {
                    "message": f"retest x{n_prev}", "site": part.site,
                    "part_id": part.part_id, "soft_bin": part.soft_bin,
                })
        return [merged[key] for key in sorted(merged)]

    # ---------------------------------------------------------------- resume
    def _load_resume_state(self) -> None:
        if not self.resume_enabled or not self.resume_state_path.is_file():
            return
        try:
            self._resume_state = json.loads(self.resume_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._resume_state = {}

    def _save_resume_state(self, unit_index: int, touchdowns_done: int) -> None:
        if not self.resume_enabled:
            return
        state = {"unit_index": unit_index, "touchdowns_done": touchdowns_done,
                 "stage": self.stage, "seed": self.seed}
        try:
            self.resume_state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:  # pragma: no cover - disk full etc.
            pass

    # --------------------------------------------------------------- reports
    @staticmethod
    def _notify(sink: Any, hook: str, *args: Any) -> None:
        """Call an optional sink hook (sinks may implement only a subset)."""
        func = getattr(sink, hook, None)
        if func is None:
            return
        try:
            func(*args)
        except Exception:  # noqa: BLE001 - a broken sink must not abort the run
            _LOG.exception("datalog sink %s failed in %s", type(sink).__name__, hook)

    def _aggregate_uph(self, summary: RunSummary) -> float:
        if not summary.units:
            return 0.0
        return sum(unit.uph for unit in summary.units) / len(summary.units)

    def _write_reports(self, summary: RunSummary) -> None:
        path = summary.write(self.output_dir / "summary.json")
        summary.files["summary"] = str(path)
        try:
            lines = [f"LOT {summary.lot_id} STAGE {summary.stage} PROGRAM {summary.program}"]
            for unit in summary.units:
                lines.append(
                    f"  {unit.kind.upper():6s} {unit.unit_id:10s} parts={unit.tested:5d} "
                    f"pass={unit.passed:5d} yield={unit.yield_pct:6.2f}% "
                    f"uph={unit.uph:8.1f}"
                )
            lines.append(
                f"TOTAL parts={summary.total_tested} pass={summary.total_passed} "
                f"yield={summary.yield_pct:.2f}% uph={summary.uph:.1f} "
                f"duration={summary.duration_s:.2f}s"
            )
            (self.output_dir / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    def part_results(self, unit_id: str) -> list[PartResult]:
        """Parts collected for a unit (used by reports/tests)."""
        return list(self._parts_by_unit.get(unit_id, []))
