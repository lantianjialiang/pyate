"""Lot level orchestration: walk the configured stage flow with KGD carry-over.

A lot flows through several stages (typically ``CP1 -> CP2 -> FT1``).  Dies that
fail an early stage must not be tested later, so this module keeps a
*known-good-die* (KGD) set between stages:

* a **wafer stage** (``CP*``) receives the KGD set as a die filter, so the prober
  only touches dies that survived the previous stage;
* a **tray stage** (``FT*``) receives one part id per KGD die, so the handler only
  loads the parts that are still alive.

Both rules are deliberately simple (see ``docs/DECISIONS.md``, D18).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..datalog.factory import build_sinks
from ..engine.controller import RunController, RunSummary
from ..engine.events import EventBus
from .session import Session

__all__ = ["StageOutcome", "LotReport", "LotRunner", "run_lot"]

#: A known-good die: ``(wafer_id, x, y)``.
Die = tuple[str, int, int]


def _is_wafer_stage(stage: str) -> bool:
    return stage.upper().startswith("CP")


@dataclass
class StageOutcome:
    """Result of one stage inside a lot flow."""

    stage: str
    summary: RunSummary
    kgd: set[Die] = field(default_factory=set)
    die_filter: set[Die] | None = None
    part_ids: list[str] | None = None
    session: str = ""

    @property
    def tested(self) -> int:
        """Parts tested in this stage."""
        return self.summary.total_tested

    @property
    def passed(self) -> int:
        """Parts that passed this stage."""
        return self.summary.total_passed

    @property
    def yield_pct(self) -> float:
        """Stage yield in percent."""
        return self.summary.yield_pct

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view."""
        data = self.summary.to_dict()
        data.update({
            "stage": self.stage,
            "session": self.session,
            "kgd_count": len(self.kgd),
            "filtered_in": None if self.die_filter is None else len(self.die_filter),
            "part_ids": None if self.part_ids is None else len(self.part_ids),
        })
        return data


@dataclass
class LotReport:
    """Aggregate report of every stage a lot went through."""

    lot_id: str
    stages: list[StageOutcome]
    output_dir: Path

    @property
    def first_pass_yield(self) -> float:
        """Yield of the first stage (usually the CP1 baseline)."""
        return self.stages[0].yield_pct if self.stages else 0.0

    @property
    def final_yield(self) -> float:
        """Yield of the last stage."""
        return self.stages[-1].yield_pct if self.stages else 0.0

    def cumulative_yield(self) -> float:
        """Product of the per-stage yields (percent)."""
        value = 100.0
        for stage in self.stages:
            value *= stage.yield_pct / 100.0
        return value

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view used by ``lot_report.json``."""
        return {
            "lot_id": self.lot_id,
            "stages": [stage.to_dict() for stage in self.stages],
            "first_pass_yield": round(self.first_pass_yield, 3),
            "final_yield": round(self.final_yield, 3),
            "cumulative_yield": round(self.cumulative_yield(), 3),
            "output_dir": str(self.output_dir),
        }

    def write(self, path: str | Path | None = None) -> Path:
        """Write ``lot_report.json`` (and a short markdown summary)."""
        target = Path(path) if path is not None else self.output_dir / "lot_report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
        lines = [
            f"# Lot {self.lot_id}",
            "",
            "| stage | tested | passed | yield % | KGD |",
            "| --- | --- | --- | --- | --- |",
        ]
        for stage in self.stages:
            # only wafer stages carry a die level KGD set; FT works on part ids
            kinds = {unit.kind for unit in stage.summary.units}
            kgd = str(len(stage.kgd)) if "wafer" in kinds else "-"
            lines.append(
                f"| {stage.stage} | {stage.tested} | {stage.passed} | "
                f"{stage.yield_pct:.2f} | {kgd} |"
            )
        lines += ["", f"Cumulative yield: **{self.cumulative_yield():.2f} %**", ""]
        (target.parent / "lot_report.md").write_text("\n".join(lines), encoding="utf-8")
        return target


class LotRunner:
    """Runs the stage flow of one lot, carrying the KGD set forward."""

    def __init__(
        self,
        session: Session,
        *,
        stages: Sequence[str] | None = None,
        sessions: dict[str, Session] | None = None,
        output_dir: str | Path | None = None,
        events: EventBus | None = None,
        stdf: bool = True,
        stats: bool = True,
        sinks: Iterable[Any] | None = None,
        retest_keep: str = "last",
    ) -> None:
        self.session = session
        self.sessions = dict(sessions or {})
        self.stages = list(stages) if stages else None
        self.events = events or EventBus()
        self.stdf = stdf
        self.stats = stats
        self.sinks = list(sinks) if sinks is not None else None
        self.retest_keep = retest_keep
        root = Path(output_dir) if output_dir is not None else None
        self.output_dir = root

    # ------------------------------------------------------------------ plan
    def planned_stages(self) -> list[str]:
        """Stages to run: explicit, else ``lot.flow``, else the run stage."""
        if self.stages:
            return list(self.stages)
        loaded = self.session.load()
        flow = list(getattr(loaded.lot, "flow", []) or [])
        return flow or [loaded.stage]

    # ------------------------------------------------------------------- run
    def session_for(self, stage: str) -> Session:
        """Session that drives ``stage`` (per-stage override or the default)."""
        return self.sessions.get(stage, self.session)

    def run(self) -> LotReport:
        """Run every stage in order and return the aggregate report."""
        stages = self.planned_stages()
        loaded = self.session.load()
        lot_id = str(getattr(loaded.lot, "lot_id", "LOT") or "LOT")
        root = self.output_dir or Path(loaded.output_dir)
        root.mkdir(parents=True, exist_ok=True)
        outcomes: list[StageOutcome] = []
        kgd: set[Die] | None = None
        for stage in stages:
            outcome = self._run_stage(stage, kgd, root)
            outcomes.append(outcome)
            if outcome.kgd:
                kgd = outcome.kgd if kgd is None else (kgd & outcome.kgd)
            elif _is_wafer_stage(stage):
                kgd = set()
        report = LotReport(lot_id=lot_id, stages=outcomes, output_dir=root)
        report.write()
        return report

    def _run_stage(self, stage: str, kgd: set[Die] | None, root: Path) -> StageOutcome:
        api_session = self.session_for(stage)
        session = api_session.load(force=True)
        site_count = int(session.site_count())
        stage_dir = root / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        die_filter = kgd if _is_wafer_stage(stage) and kgd is not None else None
        part_ids = self._part_ids_for(stage, kgd)
        sinks = self.sinks
        if sinks is None:
            sinks = build_sinks(session, stage=stage, output_dir=stage_dir,
                                site_count=site_count, stdf=self.stdf, stats=self.stats)
        controller = RunController(
            session,
            events=self.events,
            site_count=site_count,
            sinks=sinks,
            stage=stage,
            output_dir=stage_dir,
            resume=False,
            die_filter=die_filter,
            part_ids=part_ids,
            retest_keep=self.retest_keep,
        )
        summary = controller.run()
        passed = self._kgd_from(summary, int(session.binning.pass_bin))
        return StageOutcome(stage=stage, summary=summary, kgd=passed,
                            die_filter=die_filter, part_ids=part_ids,
                            session=str(session.path))

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _part_ids_for(stage: str, kgd: set[Die] | None) -> list[str] | None:
        """One FT part id per surviving die (``W01-X3Y-4``)."""
        if kgd is None or _is_wafer_stage(stage):
            return None
        return [f"{wafer}-X{x}Y{y}" for wafer, x, y in sorted(kgd)]

    @staticmethod
    def _kgd_from(summary: RunSummary, pass_bin: int) -> set[Die]:
        """Collect the dies that passed a wafer stage."""
        good: set[Die] = set()
        for unit in summary.units:
            if unit.kind != "wafer":
                continue
            for key, soft_bin in unit.bins.items():
                if soft_bin != pass_bin:
                    continue
                try:
                    x, y = key
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
                good.add((str(unit.unit_id), int(x), int(y)))
        return good


def run_lot(
    session_path: str | Path,
    overrides: dict[str, Any] | None = None,
    *,
    stages: Sequence[str] | None = None,
    stage_sessions: dict[str, str | Path] | None = None,
    output_dir: str | Path | None = None,
    events: EventBus | None = None,
) -> LotReport:
    """Convenience wrapper: ``run_lot("configs/demo_cp/session.yaml")``."""
    sessions = {name: Session(path, overrides)
                for name, path in (stage_sessions or {}).items()}
    runner = LotRunner(Session(session_path, overrides), stages=stages,
                       sessions=sessions, output_dir=output_dir, events=events)
    return runner.run()
