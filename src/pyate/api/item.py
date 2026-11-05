"""Execute a single flow item outside a lot run.

A normal run walks a wafer (or a tray), applies the whole flow and bins every
part. When one test item is misbehaving you want its value on one site and
nothing else, so this module builds the same HAL, DUT, limits, binning and
temperature the engine would use, then executes exactly one node.

Results are published as ``TEST_RESULT`` events, which is what fills the Run
page datalog. No part is reported: a partial flow has no meaningful bin or
yield, and inventing one would pollute the wafer map and the summary.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..config.validators import resolve_pattern_path
from ..dut.registry import create_dut
from ..engine.binning import BinningEngine
from ..engine.context import SiteContext
from ..engine.events import EventBus
from ..engine.executor import FlowExecutor
from ..engine.flow import iter_flow_items
from ..hal.simulated import SimulatedHAL
from ..testitems.base import LimitsTable, PatternCache
from .session import Session

__all__ = ["ItemRunReport", "run_item"]


@dataclass(frozen=True)
class ItemRunReport:
    """What one single-item execution produced."""

    name: str
    test_num: int
    stage: str
    sites: tuple[int, ...] = ()
    values: tuple[dict[str, Any], ...] = ()
    passed: bool = False
    elapsed_ms: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view (used by the GUI and by tests)."""
        return {
            "name": self.name,
            "test_num": self.test_num,
            "stage": self.stage,
            "sites": list(self.sites),
            "values": [dict(row) for row in self.values],
            "passed": self.passed,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "message": self.message,
        }

    def text(self) -> str:
        """One line for a status bar or a log pane."""
        head = f"{self.name} #{self.test_num} ({self.stage})"
        if self.message:
            return f"{head}: {self.message}"
        if not self.values:
            return f"{head}: no result"
        if len(self.values) > 4:
            # multi-pin items (continuity) would make an unreadable line
            failures = sum(1 for row in self.values if not row.get("passed"))
            body = (f"{len(self.values)} values, {len(self.values) - failures} pass, "
                    f"{failures} fail")
        else:
            parts: list[str] = []
            for row in self.values:
                value = row.get("value")
                shown = "-" if value is None else f"{float(value):.6g}"
                unit = str(row.get("unit") or "")
                verdict = "pass" if row.get("passed") else "FAIL"
                parts.append(f"S{row.get('site')} {shown}{(' ' + unit) if unit else ''} {verdict}")
            body = ", ".join(parts)
        return f"{head}: {body}  ({self.elapsed_ms:.0f} ms)"


def run_item(
    session: Session,
    *,
    name: str,
    test_num: int,
    stage: str | None = None,
    sites: Sequence[int] | None = None,
    params: dict[str, Any] | None = None,
    events: EventBus | None = None,
) -> ItemRunReport:
    """Run one item of ``stage`` and publish its results on ``events``.

    ``params`` replaces the node's parameters for this execution only (the
    GUI passes whatever is currently in the parameter form, so "Run item"
    uses what you see even before "Apply to node" was pressed).
    """
    loaded = session.load()
    stage = stage or loaded.stage
    node = _find_node(loaded.program.flow(stage), name, test_num)
    if node is None:
        return ItemRunReport(name=name, test_num=test_num, stage=stage,
                             message="item not found in this stage")
    if not node.enabled:
        return ItemRunReport(name=name, test_num=test_num, stage=stage,
                             message="item is unchecked in the flow")
    if params is not None:
        node = node.model_copy(update={"params": dict(params)})

    chosen = list(sites) if sites else _enabled_sites(loaded)
    if not chosen:
        return ItemRunReport(name=name, test_num=test_num, stage=stage,
                             message="no site enabled")
    bus = events or EventBus()

    hal = SimulatedHAL.from_session(loaded, site_count=max(chosen))
    levels = loaded.levels_for()
    temperature = loaded.temperature()
    limits = LimitsTable(loaded.limits)
    binning = BinningEngine(loaded.binning)

    def resolver(pattern_name: str) -> Any:
        path = resolve_pattern_path(loaded, pattern_name)
        if path is None:
            raise FileNotFoundError(f"pattern '{pattern_name}' not found")
        return path

    executor = FlowExecutor(
        program_nodes=[node],
        stage=stage,
        limits=limits,
        binning=binning,
        hal=hal,
        events=bus,
        temperature=temperature,
        pattern_loader=PatternCache(resolver).get,
        overhead_ms=0.0,
    )

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    passed = True
    for site in chosen:
        # one DUT per site: item state (counter, shift register, ...) advances
        # while the item runs, so sites must not share an instance. The nominal
        # part is reproducible, which is the point of a debug run.
        dut = create_dut(loaded.dut_profile, seed=int(loaded.run.seed),
                         sample_yield=False, variation=False)
        dut.set_temperature(temperature)
        hal.bind_dut(int(site), dut)
        supply = hal.site(int(site)).dps
        supply.set_voltage("VDD", float(levels.vdd.nom))
        supply.connect("VDD")
        ctx = SiteContext(site_id=int(site), dut=dut, pin_map=hal.pin_map(int(site)),
                          part_id=f"ITEM-{int(test_num)}")
        for result in executor.run_single(ctx, node):
            if result.skipped:
                continue
            payload = result.to_dict()
            payload["site"] = int(site)
            rows.append(payload)
            passed = passed and bool(result.passed)
    elapsed = 1000.0 * (time.perf_counter() - started)
    return ItemRunReport(name=str(node.item), test_num=int(node.test_num or test_num),
                         stage=stage, sites=tuple(int(site) for site in chosen),
                         values=tuple(rows), passed=passed, elapsed_ms=elapsed)


def _find_node(nodes: list[Any], name: str, test_num: int) -> Any:
    """The item node matching ``name`` (and ``test_num`` when duplicated)."""
    matches = [node for node in iter_flow_items(nodes)
               if str(node.item) == str(name) and int(node.test_num or 0) == int(test_num)]
    return matches[0] if matches else None


def _enabled_sites(loaded: Any) -> list[int]:
    """Site numbers the session would run (configured minus disabled)."""
    disabled = {int(site) for site in getattr(loaded.run, "disabled_sites", []) or []}
    return [site for site in range(1, int(loaded.site_count()) + 1) if site not in disabled]
