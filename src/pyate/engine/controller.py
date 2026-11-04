"""RunController: drives lots/units/touchdowns and produces the run summary."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .._files import replace_atomic
from ..config import LoadedSession
from ..config.loader import write_snapshot
from ..dut.identity import DUTInstanceSpec, PhysicalPartId
from ..dut.registry import YieldSampler, create_dut
from ..hal.base import HALBackend
from ..hal.simulated import SimulatedHAL
from ..scenario.base import UnitPlan
from ..testitems.base import LimitsTable, PatternCache
from .binning import BinningEngine
from .context import PartResult, SiteContext, TouchdownInfo
from .events import EventBus, EventKind
from .executor import FlowExecutor, RunAborted
from .journal import RunJournal
from .metrics import EquipmentStats
from .retest import RetestConfig, merge_attempt, should_retest
from .site import SiteScheduler

__all__ = ["DatalogSink", "RunController", "RunSummary", "UnitSummary"]

_LOG = logging.getLogger("pyate.controller")


class DatalogSink:
    """Minimal sink interface (all methods optional)."""
    required = True

    def on_run_start(self, controller: RunController) -> None:
        """Called before the first unit."""

    def on_unit_start(self, unit: UnitPlan) -> None:
        """Called when a wafer/tray starts."""

    def on_part(self, part: PartResult, unit: UnitPlan) -> None:
        """Called for every finished part."""

    def on_unit_end(self, summary: UnitSummary) -> None:
        """Called when a wafer/tray finishes."""

    def on_run_end(self, summary: RunSummary) -> None:
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
    status: str = "DONE"
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
            "status": self.status,
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
        retest_policy: RetestConfig | None = None,
        population: dict[str, DUTInstanceSpec] | None = None,
    ) -> None:
        self.session = session
        self.events = events or EventBus()
        self.stage = stage or session.stage
        self.seed = int(seed if seed is not None else session.run.seed)
        self.site_count = int(site_count or session.site_count())
        from ..plugins import get_manager
        self.plugins = get_manager()
        self.sinks: list[DatalogSink] = list(sinks or [])
        backend_factory = self.plugins.registry.instruments.get(session.run.backend)
        if hal is not None:
            self.hal = hal
        elif session.run.backend == "simulated":
            self.hal = SimulatedHAL.from_session(session, site_count=self.site_count)
        elif backend_factory is not None:
            self.hal = backend_factory(session=session, site_count=self.site_count)
        else:
            raise ValueError(f"unknown HAL backend {session.run.backend!r}")
        self.limits = LimitsTable(session.limits)
        self.binning = BinningEngine(session.binning)
        if session.run.binning_strategy:
            self.binning = self.plugins.registry.binning_strategies[session.run.binning_strategy](session.binning)
        self.patterns = PatternCache(self._pattern_path)
        self.output_dir = Path(output_dir or session.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.retest_bins = set(retest_bins if retest_bins is not None else session.run.retest_bins)
        self.max_retries = int(max_retries if max_retries is not None else session.run.max_retries)
        self.retest_keep = retest_keep
        self.retest = RetestConfig(bins=sorted(int(b) for b in self.retest_bins),
                                   max_retries=self.max_retries, keep=retest_keep)
        if retest_policy is not None:
            self.retest = retest_policy
        self.population = population if population is not None else {}
        self.journal: RunJournal | None = None
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
        self.summary: RunSummary | None = None
        self._resume_state: dict[str, Any] = {}
        self._sink_errors: list[dict[str, str]] = []
        self._site_scheduler: SiteScheduler | None = None
        self._profile_json = self.session.dut_profile.model_dump_json(by_alias=True)
        self.hal.cancel_check = self._check_cancel

    def _check_cancel(self) -> None:
        if self._stop.is_set():
            raise RunAborted("run aborted by user")

    # --------------------------------------------------------------- helpers
    def _pattern_path(self, name: str) -> Any:
        """Resolve a pattern name to an absolute path (used by PatternCache)."""
        from ..config.validators import resolve_pattern_path

        path = resolve_pattern_path(self.session, name)
        if path is None:
            raise FileNotFoundError(f"pattern '{name}' not found")
        return path

    def _build_scenario(self) -> Any:
        if self.session.run.scenario:
            factory = self.plugins.registry.scenarios[self.session.run.scenario]
            return factory(self.session, self.site_count, seed=self.seed)
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
            hooks=self.plugins.bridge,
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
        """Execute or replay committed touchdowns, rebuilding all outputs."""
        self._load_resume_state()
        self.events.publish(EventKind.LOT_START, {
            "lot_id": self.session.lot.lot_id if self.session.lot else self.session.name,
            "stage": self.stage,
        })
        summary = RunSummary(
            lot_id=self.session.lot.lot_id if self.session.lot else self.session.name,
            stage=self.stage, program=self.session.program.program, seed=self.seed,
            started_at=__import__("time").time(),
        )
        failure: Exception | None = None
        try:
            self.plugins.call("before_lot", run=self)
            summary.files["snapshot"] = str(write_snapshot(self.session, self.output_dir))
            for sink in self.sinks:
                self._notify(sink, "on_run_start", self)
            scheduler = SiteScheduler(self._executor, mode=self.session.run.parallel_mode,
                                      events=self.events)
            self._site_scheduler = scheduler
            for unit_index, unit in enumerate(self.scenario.units()):
                if self._stop.is_set():
                    break
                unit_summary = self._run_unit(unit, scheduler, unit_index)
                summary.units.append(unit_summary)
                summary.total_parts += unit_summary.parts
                summary.total_tested += unit_summary.tested
                summary.total_passed += unit_summary.passed
                for key, value in unit_summary.bin_counts.items():
                    summary.bin_counts[key] = summary.bin_counts.get(key, 0) + value
                summary.files[f"unit_{unit.unit_id}"] = str(self.output_dir / f"{unit.unit_id}_summary.json")
                if not self._stop.is_set():
                    self._save_resume_state(unit_index + 1, 0)
        except Exception as exc:
            failure = exc
        finally:
            summary.aborted = self._stop.is_set()
            summary.status = "FAILED" if failure else ("ABORTED" if summary.aborted else "DONE")
            summary.finished_at = __import__("time").time()
            summary.uph = self._aggregate_uph(summary)
            summary.meta["retests"] = sum(int(unit.meta.get("retests", 0) or 0) for unit in summary.units)
            try:
                self.plugins.call("after_lot", summary=summary)
            except Exception as exc:
                failure = failure or exc
            self.summary = summary
            for sink in self.sinks:
                for hook, args in (("on_run_end", (summary,)), ("close", ())):
                    try:
                        self._notify(sink, hook, *args)
                    except Exception as exc:
                        failure = failure or exc
            try:
                if self._site_scheduler is not None:
                    self._site_scheduler.close()
                    self._site_scheduler = None
                self.hal.cancel_check = None
                self.hal.reset()
            except Exception as exc:
                failure = failure or exc
                summary.meta.setdefault("cleanup_errors", []).append(str(exc))
            finally:
                if self.journal is not None:
                    try:
                        self.journal.close()
                    except Exception as exc:
                        failure = failure or exc
                        summary.meta.setdefault("cleanup_errors", []).append(str(exc))
                    self.journal = None
            if failure is not None:
                summary.status = "FAILED"
                summary.meta["error"] = str(failure)
            if self._sink_errors:
                summary.meta["optional_sink_errors"] = list(self._sink_errors)
            self._write_reports(summary)
            self.events.publish(EventKind.LOT_END, summary.to_dict())
        if failure is not None:
            raise failure
        return summary

    # ------------------------------------------------------------------ unit
    def _run_unit(self, unit: UnitPlan, scheduler: SiteScheduler, unit_index: int) -> UnitSummary:
        unit_summary = UnitSummary(
            unit_id=unit.unit_id, kind=unit.kind,
            index_time_ms=float(unit.meta.get("index_time_ms", 0.0)),
            change_time_ms=unit.change_time_ms,
            meta={k: v for k, v in unit.meta.items() if k not in ("wafer_map", "die_filter")},
        )
        unit_event: dict[str, Any] = {"unit": unit.unit_id, "kind": unit.kind}
        if unit.kind == "tray":
            unit_event["tray_cols"] = int(unit.meta.get("tray_cols") or 0)
            unit_event["tray_rows"] = int(unit.meta.get("tray_rows") or 0)
            unit_event["tray_parts"] = int(unit.meta.get("tray_parts") or 0)
        self.events.publish(EventKind.UNIT_START, unit_event)
        self.events.publish(
            EventKind.WAFER_START if unit.kind == "wafer" else EventKind.UNIT_START,
            dict(unit_event),
        )
        for sink in self.sinks:
            self._notify(sink, "on_unit_start", unit)
        self.plugins.call("before_unit", unit=unit)
        wafer_map = unit.meta.get("wafer_map")
        bins: dict[Any, int] = {}
        unit_seed = (self.seed ^ zlib.crc32(unit.unit_id.encode("utf-8"))) & 0xFFFFFFFF
        sampler = YieldSampler(
            self.session.dut_profile.yield_,
            unit_seed,
            wafer_radius=wafer_map.radius_mm if wafer_map is not None else None,
        )
        touchdown_count = int(unit.meta.get("touchdown_count", 0))
        committed_touchdowns = 0
        unit_start = __import__("time").time()
        live_sites = max(1, self.site_count - len(self.session.run.disabled_sites))
        equipment = EquipmentStats(sites=live_sites, ideal_test_ms=self.session.run.ideal_test_ms)
        for touch_index, touchdown in enumerate(unit.touchdowns):
            if self._stop.is_set():
                break
            parts = self.journal.get(unit.unit_id, touchdown.touch_id) if self.journal else None
            replayed = parts is not None
            if parts is None:
                parts = self._run_touchdown(touchdown, unit, sampler, unit_seed)
                if self._stop.is_set():
                    break  # an incomplete touchdown is never committed
            else:
                for part in parts:
                    physical = part.extra.get("physical_instance")
                    if physical:
                        instance = DUTInstanceSpec.from_dict(physical)
                        existing = self.population.get(instance.identity.key)
                        if existing is not None and existing != instance:
                            raise ValueError("resume physical population mismatch")
                        self.population[instance.identity.key] = instance
                    for result in part.results:
                        self.events.publish(EventKind.TEST_RESULT, {
                            "result": result.to_dict(), "site": part.site,
                            "part_id": part.part_id, "replayed": True,
                        })
                    self.events.publish(EventKind.PART_END, {"part": part.to_dict(), "site": part.site})
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
                touchdown_ms = max(touchdown_ms, float(part.extra.get("touchdown_test_ms", part.test_time_ms)))
                key: Any = (part.x, part.y) if unit.kind == "wafer" else part.part_id
                bins[key] = part.soft_bin
                for sink in self.sinks:
                    self._notify(sink, "on_part", part, unit)
            unit_summary.test_time_ms += touchdown_ms
            jam_ms = 0.0
            if touchdown.xtra.get("jam"):
                jam_ms = float(touchdown.xtra.get("jam_recovery_ms", 0.0))
                self.events.publish(EventKind.LOG, {"message": "handler jam recovered",
                                                    "unit": unit.unit_id})
            tested_now = sum(1 for part in parts if not part.skipped)
            passed_now = sum(1 for part in parts if not part.skipped and part.passed)
            equipment.add_touchdown(
                tested=tested_now,
                passed=passed_now,
                test_ms=touchdown_ms,
                index_ms=float(touchdown.index_time_ms),
                jam_ms=jam_ms,
            )
            if self.journal and not replayed:
                self.journal.commit(unit.unit_id, touchdown.touch_id, parts)
            self._save_resume_state(unit_index, touch_index + 1)
            committed_touchdowns += 1
            figures = equipment.to_dict()
            self.events.publish(EventKind.PROGRESS, {
                "unit": unit.unit_id,
                "touchdown": touch_index + 1,
                "touchdowns": touchdown_count,
                "uph": figures["uph"],
                "equipment": figures,
            })
        elapsed = (__import__("time").time() - unit_start) * 1000.0
        if not self._stop.is_set() and equipment.tested:
            equipment.downtime_ms += unit.change_time_ms
        figures = equipment.to_dict()
        unit_summary.uph = round(equipment.uph, 2)
        unit_summary.meta["equipment"] = figures
        unit_summary.meta["wall_ms"] = round(elapsed, 3)
        unit_summary.meta["touchdowns"] = touchdown_count or committed_touchdowns
        unit_summary.meta["wafer_id"] = unit.unit_id
        if wafer_map is not None:
            unit_summary.bins = dict(bins.items())
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
        self.plugins.call("after_unit", summary=unit_summary)
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
            ctx.extra["program_revision"] = self.session.program.revision
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
            lot_id = self.session.lot.lot_id if self.session.lot else self.session.name
            identity = PhysicalPartId(lot_id, part_id)
            if identity.key not in self.population:
                wafer = self.session.wafer if unit.kind == "wafer" else None
                dx, dy = wafer.die_size_mm if wafer else (0.0, 0.0)
                cx, cy = wafer.center_die if wafer else (0, 0)
                self.population[identity.key] = DUTInstanceSpec(
                    identity, self.seed, (x - cx) * dx, (y - cy) * dy,
                    self._profile_json,
                    wafer_map_radius(wafer), unit_seed,
                )
            instance = self.population[identity.key]
            ctx.extra["instance"] = instance
            ctx.extra["physical_instance"] = dataclasses.asdict(instance)
            dut = create_dut(self.session.dut_profile, instance=instance)
            dut.set_temperature(self.session.temperature())
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
        scheduler = self._site_scheduler
        if scheduler is None:
            scheduler = SiteScheduler(self._executor, mode=self.session.run.parallel_mode, events=self.events)
            self._site_scheduler = scheduler
        return scheduler.run_touchdown(contexts)

    def _apply_retest(self, results: list[PartResult], contexts: list[SiteContext],
                      unit: UnitPlan, sampler: YieldSampler, unit_seed: int) -> list[PartResult]:
        policy = self.retest
        total_ms = max((part.test_time_ms for part in results), default=0.0)
        merged = {part.site: part for part in results}
        retries = max([policy.max_retries, *[int(p.extra.get("retest_requested", 0)) for p in results]])
        for attempt in range(1, retries + 1):
            retry_sites = [
                part.site for part in merged.values()
                if should_retest(part, policy, attempt) or (
                    not part.passed and not part.skipped and attempt <= int(part.extra.get("retest_requested", 0))
                )
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
                dut = create_dut(self.session.dut_profile, instance=ctx.extra["instance"])
                dut.set_temperature(self.session.temperature())
                self.hal.bind_dut(site, dut)
                ctx.dut = dut
            retry_contexts = [item for item in contexts if item.site_id in retry_sites]
            retry_parts = self._scheduler_run(retry_contexts)
            total_ms += max((part.test_time_ms for part in retry_parts), default=0.0)
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
            part.extra["touchdown_test_ms"] = total_ms
            if part.retest:
                n_prev = len(part.extra.get("previous_attempts", []))
                self.events.publish(EventKind.LOG, {
                    "message": f"retest x{n_prev}", "site": part.site,
                    "part_id": part.part_id, "soft_bin": part.soft_bin,
                })
        return [merged[key] for key in sorted(merged)]

    # ---------------------------------------------------------------- resume
    def _load_resume_state(self) -> None:
        snapshot = self.session.to_snapshot()
        snapshot.pop("files", None)
        snapshot["effective_stage"] = self.stage
        snapshot["effective_seed"] = self.seed
        snapshot["effective_sites"] = self.site_count
        snapshot["effective_retest"] = dataclasses.asdict(self.retest)
        snapshot["effective_filter"] = None if self.die_filter is None else sorted(self.die_filter)
        snapshot["effective_part_ids"] = self.part_ids
        snapshot["extensions"] = self.session.spec.extensions
        patterns = {}
        from ..config.validators import iter_flow_items
        for nodes in self.session.program.flows.values():
            for node in iter_flow_items(nodes):
                name = node.params.get("pattern")
                if name:
                    pattern = self.patterns.get(name)
                    for file in [pattern.source, *pattern.includes]:
                        if file:
                            patterns[str(file)] = hashlib.sha256(Path(file).read_bytes()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([snapshot, patterns], sort_keys=True).encode()).hexdigest()
        self.journal = RunJournal(self.output_dir / "results.sqlite3", fingerprint, reset=not self.resume_enabled)

    def _save_resume_state(self, unit_index: int, touchdowns_done: int) -> None:
        state = {"unit_index": unit_index, "touchdowns_done": touchdowns_done,
                 "stage": self.stage, "seed": self.seed}
        try:
            temporary = self.resume_state_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            replace_atomic(temporary, self.resume_state_path)
        except OSError as exc:
            raise OSError("cannot commit resume checkpoint") from exc

    # --------------------------------------------------------------- reports
    def _notify(self, sink: Any, hook: str, *args: Any) -> None:
        """Call an optional sink hook (sinks may implement only a subset)."""
        func = getattr(sink, hook, None)
        if func is None:
            return
        try:
            func(*args)
        except Exception as exc:
            if getattr(sink, "required", True):
                raise OSError(f"required datalog sink {type(sink).__name__}.{hook} failed: {exc}") from exc
            detail = {"sink": type(sink).__name__, "hook": hook, "error": str(exc)}
            if detail not in self._sink_errors:
                self._sink_errors.append(detail)
                self.events.publish(EventKind.ERROR, {**detail, "optional": True})
            _LOG.exception("optional datalog sink %s failed in %s", type(sink).__name__, hook)

    def _aggregate_uph(self, summary: RunSummary) -> float:
        if not summary.units:
            return 0.0
        milliseconds = sum(float(unit.meta.get("equipment", {}).get("planned_ms", 0)) for unit in summary.units)
        return summary.total_tested * 3_600_000 / milliseconds if milliseconds > 0 else 0.0

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
        return list(self.iter_part_results(unit_id))

    def iter_part_results(self, unit_id: str, *, offset: int = 0,
                          limit: int | None = None) -> Iterable[PartResult]:
        """Read committed part history without retaining every result in RAM."""
        return RunJournal.read_parts(self.output_dir / "results.sqlite3", unit_id,
                                     offset=offset, limit=limit)


def wafer_map_radius(wafer: Any) -> float | None:
    """Physical wafer radius for population sampling."""
    return None if wafer is None else float(wafer.diameter_mm) / 2.0
