"""Qt-free view models for the GUI (unit testable in headless mode).

The view models subscribe to the :class:`~pyate.engine.events.EventBus` and keep a
bounded, throttled snapshot of the run state. The Qt layer only renders what the
view model exposes, which keeps the 10k-events-per-second case smooth: rendering
happens at most once per ``throttle_ms``.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from ...api import Event, EventBus, EventKind, normalize_shmoo

__all__ = ["DIFFICULTY_BINS", "DatalogRow", "LogEntry", "RunViewModel", "TestSummaryRow"]


@dataclass(frozen=True)
class DatalogRow:
    """One measured value, ready for a table model."""

    seq: int
    site: int
    part: str
    test_num: int
    name: str
    value: float | None
    unit: str
    lo: float | None
    hi: float | None
    passed: bool
    #: Logical pin the result was measured on (``""`` for functional tests).
    pin: str = ""

    def as_tuple(self) -> tuple[Any, ...]:
        """Row content in display order."""
        return (self.site, self.part, self.test_num, self.name, self.value, self.unit,
                self.lo, self.hi, "PASS" if self.passed else "FAIL")


@dataclass(frozen=True)
class LogEntry:
    """A LOG / ERROR event."""

    seq: int
    level: str
    message: str


@dataclass(frozen=True)
class TestSummaryRow:
    """One row of the run-page test summary.

    ``site`` / ``wafer`` / ``pin`` are ``None`` when the row combines that
    dimension. ``pin`` is the logical pin the test measures, which is how a
    multi-pin item (continuity, VOL/VOH) is split apart.
    """

    test_num: int
    name: str
    site: int | None
    wafer: str | None
    pin: str | None
    count: int
    passed: int
    failed: int
    mean: float | None
    stdev: float | None
    minimum: float | None
    maximum: float | None
    lo: float | None = None
    hi: float | None = None
    cp: float | None = None
    cpk: float | None = None


def _spec_limit(value: Any) -> float | None:
    """A finite spec limit. STDF's ±1e38 sentinels mean unbounded."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 1e30:
        return None
    return number


def _group_names(group: str | None, *, by_site: bool,
                groups: Iterable[str] | None) -> set[str]:
    """Which dimensions split a summary row. ``test`` means none of them."""
    if groups is not None:
        return {str(name) for name in groups if name and name != "test"}
    if group is None:
        group = "site" if by_site else "test"
    if group == "test":
        return set()
    return {str(group)}


def _cp_cpk(mean: float | None, stdev: float | None,
            lo: float | None, hi: float | None) -> tuple[float | None, float | None]:
    """Cp needs both limits; Cpk needs one. Same sample-sigma formula as the datalog."""
    if mean is None or stdev is None:
        return None, None
    if stdev <= 0:
        return None, None
    cp = (hi - lo) / (6.0 * stdev) if lo is not None and hi is not None else None
    sides: list[float] = []
    if lo is not None:
        sides.append((mean - lo) / (3.0 * stdev))
    if hi is not None:
        sides.append((hi - mean) / (3.0 * stdev))
    return cp, (min(sides) if sides else None)


@dataclass
class _Acc:
    """Running count for one ``(test_num, site, wafer, pin)`` bucket."""

    name: str
    count: int = 0
    passed: int = 0
    numeric: int = 0
    total: float = 0.0
    total_sq: float = 0.0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    lo: float | None = None
    hi: float | None = None


#: Soft bins that count as failing (used for the yield preview before binning).
DIFFICULTY_BINS = (2, 3, 4, 5, 6, 7, 8, 9)


def _synchronized(func):
    @wraps(func)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return func(self, *args, **kwargs)
    return wrapped


class RunViewModel:
    """Aggregates run events into a render friendly snapshot."""

    def __init__(self, *, max_rows: int = 500, max_logs: int = 500,
                 throttle_ms: int = 50, max_history_groups: int = 1000) -> None:
        self.max_rows = int(max_rows)
        self.max_logs = int(max_logs)
        self.throttle_ms = int(throttle_ms)
        self.max_history_groups = int(max_history_groups)
        if min(self.max_rows, self.max_logs, self.max_history_groups) < 1:
            raise ValueError("view model buffer sizes must be positive")
        self.events: EventBus | None = None
        self.lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------ setup
    def attach(self, events: EventBus) -> RunViewModel:
        """Subscribe to every interesting topic of ``events``."""
        self.events = events
        for kind in (EventKind.TEST_RESULT, EventKind.PART_END, EventKind.BIN_RESULT,
                     EventKind.PROGRESS, EventKind.UNIT_START, EventKind.UNIT_END,
                     EventKind.LOT_START, EventKind.LOT_END, EventKind.LOG,
                     EventKind.ERROR, EventKind.TOUCHDOWN, EventKind.BREAKPOINT):
            events.subscribe(kind, self.on_event)
        return self

    def detach(self) -> None:
        """Remove every subscription again."""
        if self.events is None:
            return
        for kind in (EventKind.TEST_RESULT, EventKind.PART_END, EventKind.BIN_RESULT,
                     EventKind.PROGRESS, EventKind.UNIT_START, EventKind.UNIT_END,
                     EventKind.LOT_START, EventKind.LOT_END, EventKind.LOG,
                     EventKind.ERROR, EventKind.TOUCHDOWN, EventKind.BREAKPOINT):
            self.events.unsubscribe(kind, self.on_event)
        self.events = None

    @_synchronized
    def reset(self) -> None:
        """Drop every collected value (start of a new run)."""
        self._rows: deque[DatalogRow] = deque(maxlen=self.max_rows)
        self._pending: deque[DatalogRow] = deque(maxlen=self.max_rows)
        self._logs: deque[LogEntry] = deque(maxlen=self.max_logs)
        self._pending_logs: deque[LogEntry] = deque(maxlen=self.max_logs)
        self._series: dict[int, deque[float]] = {}
        self._site_series: dict[tuple[int, int], deque[float]] = {}
        self._wafer_series: dict[tuple[int, str], deque[float]] = {}
        # keys are ``(test_num, site, wafer, pin)``: the pin is what splits a
        # multi-pin item, and unlike a part id it cannot grow per die
        self._samples: dict[tuple[int, int, str, str], deque[float]] = {}
        self._acc: dict[tuple[int, int, str, str], _Acc] = {}
        self._totals: dict[tuple[int, int, str, str], _Acc] = {}
        self._history_keys: OrderedDict[tuple[str, str], set[tuple[int, int, str, str]]] = OrderedDict()
        self.history_evictions = 0
        self._test_names: dict[int, str] = {}
        self._shmoo: dict[str, Any] = {}
        self.parts = 0
        self.passed = 0
        self.retests = 0
        self.uph = 0.0
        self.bins: dict[int, int] = {}
        self.dies: dict[tuple[int, int], int] = {}
        self.die_info: dict[tuple[int, int], dict[str, Any]] = {}
        self.site_state: dict[int, dict[str, Any]] = {}
        self.site_status: dict[int, str] = {}
        self.touch: dict[str, Any] = {}
        self.tray_cols = 0
        self.tray_rows = 0
        self.tray_total = 0
        self.tray_slots: dict[tuple[int, int], dict[str, Any]] = {}
        self.output_bins: dict[int, int] = {}
        self._sorted_bin: dict[str, int] = {}
        self.jams = 0
        self.metrics: dict[str, Any] = {}
        self.unit = ""
        self.unit_kind = ""
        self.touchdown = 0
        self.touchdowns = 0
        self.running = False
        self.finished = False
        self.aborted = False
        self.paused_at: dict[str, Any] | None = None
        self.error = ""
        self.last_event_seq = 0
        self._dirty = True
        self._last_render = 0.0
        self.total_events = 0

    # ----------------------------------------------------------------- events
    @_synchronized
    def on_event(self, event: Event) -> None:
        """Handle one event (never renders)."""
        self.total_events += 1
        self.last_event_seq = event.seq
        kind = str(event.kind)
        data = event.data
        if kind == EventKind.TEST_RESULT.value:
            self._on_test_result(event.seq, data)
        elif kind == EventKind.PART_END.value:
            self._on_part_end(data)
        elif kind == EventKind.UNIT_START.value:
            self.unit = str(data.get("unit", ""))
            self.unit_kind = str(data.get("kind", self.unit_kind))
            self.dies = {}
            self.die_info = {}
            self._load_tray(data)
            self.running = True
        elif kind == EventKind.UNIT_END.value:
            self._dirty = True
        elif kind == EventKind.TOUCHDOWN.value:
            self.touchdown = int(data.get("touchdown", 0) or 0)
            self.touch = dict(data)
            if data.get("kind"):
                self.unit_kind = str(data.get("kind"))
            self._on_touchdown(data)
            self._dirty = True
        elif kind == EventKind.PROGRESS.value:
            self.touchdowns = int(data.get("touchdowns", self.touchdowns) or 0)
            if data.get("uph") is not None:
                self.uph = float(data.get("uph") or 0.0)
            equipment = data.get("equipment")
            if isinstance(equipment, dict):
                self.metrics = dict(equipment)
                self.jams = int(equipment.get("jams") or 0)
                self.uph = float(equipment.get("uph") or self.uph)
            self._dirty = True
        elif kind == EventKind.LOT_START.value:
            self.running = True
            self.finished = False
            self.paused_at = None
        elif kind == EventKind.LOT_END.value:
            self.running = False
            self.finished = True
            self.aborted = bool(data.get("aborted", False))
        elif kind == EventKind.LOG.value:
            self._add_log("INFO", str(data.get("message", "")))
        elif kind == EventKind.BREAKPOINT.value:
            self.paused_at = {
                "test_num": int(data.get("test_num") or 0),
                "item": str(data.get("item") or ""),
                "site": int(data.get("site") or 0),
            }
            self._add_log("INFO", str(data.get("message", "breakpoint hit")))
            self.running = True
        elif kind == EventKind.ERROR.value:
            message = str(data.get("error") or data.get("message") or "")
            self._add_log("ERROR", message)
            self.error = message

    def _on_test_result(self, seq: int, data: dict[str, Any]) -> None:
        result = data.get("result") or {}
        if result.get("skipped"):
            return
        part = str(data.get("part_id") or result.get("part_id") or "")
        pin = str(result.get("pin") or data.get("pin") or "")
        row = DatalogRow(
            seq=seq,
            site=int(result.get("site") or data.get("site") or 0),
            part=part,
            test_num=int(result.get("test_num") or 0),
            name=str(result.get("test_name") or data.get("item") or ""),
            value=result.get("value"),
            unit=str(result.get("unit") or ""),
            lo=result.get("lo_limit"),
            hi=result.get("hi_limit"),
            passed=bool(result.get("passed", True)),
            pin=pin,
        )
        self._rows.append(row)
        self._pending.append(row)
        self._test_names[row.test_num] = row.name
        wafer = self.unit or ""
        key = (row.test_num, row.site, wafer, pin)
        history_key = (wafer, pin)
        self._history_keys.setdefault(history_key, set()).add(key)
        self._history_keys.move_to_end(history_key)
        while len(self._history_keys) > self.max_history_groups:
            _, expired = self._history_keys.popitem(last=False)
            for old_key in expired:
                self._samples.pop(old_key, None)
                self._acc.pop(old_key, None)
            self.history_evictions += 1
        acc = self._acc.setdefault(key, _Acc(name=row.name))
        total = self._totals.setdefault((row.test_num, row.site, wafer, ""), _Acc(name=row.name))
        _accumulate(acc, row)
        _accumulate(total, row)
        if (isinstance(row.value, (int, float)) and not isinstance(row.value, bool)
                and math.isfinite(row.value)):
            value = float(row.value)
            self._series.setdefault(row.test_num, deque(maxlen=80)).append(value)
            self._site_series.setdefault((row.test_num, row.site), deque(maxlen=80)).append(value)
            self._wafer_series.setdefault((row.test_num, wafer), deque(maxlen=80)).append(value)
            self._samples.setdefault(key, deque(maxlen=4000)).append(value)
        extra = result.get("extra") or {}
        if extra.get("shmoo"):
            self.set_shmoo(extra.get("shmoo"))
        self._dirty = True

    def _on_part_end(self, data: dict[str, Any]) -> None:
        part = data.get("part") or {}
        if part.get("skipped"):
            return
        self.parts += 1
        if part.get("passed"):
            self.passed += 1
        soft = int(part.get("soft_bin") or 0)
        self.bins[soft] = self.bins.get(soft, 0) + 1
        if part.get("retest"):
            self.retests += 1
        site = int(part.get("site") or data.get("site") or 0)
        info = {
            "bin": soft,
            "passed": bool(part.get("passed")),
            "part_id": str(part.get("part_id") or ""),
            "fail_name": str(part.get("first_fail_name") or ""),
            "fail_value": part.get("first_fail_value"),
        }
        self.site_state[site] = info
        self.site_status[site] = "pass" if info["passed"] else "fail"
        if self.unit_kind == "wafer":
            coord = (int(part.get("x") or 0), int(part.get("y") or 0))
            self.dies[coord] = soft
            self.die_info[coord] = info
        if self.unit_kind == "tray":
            self._pick_slot(part, info)
        self._dirty = True

    def _load_tray(self, data: dict[str, Any]) -> None:
        """Build the input-tray grid for a new tray. Empty when this is a wafer."""
        cols = int(data.get("tray_cols") or 0)
        rows = int(data.get("tray_rows") or 0)
        total = int(data.get("tray_parts") or 0)
        if cols <= 0 or rows <= 0 or total <= 0:
            return
        self.tray_cols = cols
        self.tray_rows = rows
        self.tray_total = total
        self.tray_slots = {}
        self.site_status = {}
        for index in range(total):
            col = index % cols
            row = index // cols
            self.tray_slots[(col, row)] = {
                "state": "untested", "part_id": "", "site": 0, "passed": None,
            }

    def _on_touchdown(self, data: dict[str, Any]) -> None:
        """Mark the slots now in the socket, and mask the sites with no part."""
        active: set[int] = set()
        for slot in data.get("slots") or []:
            site = int(slot.get("site") or 0)
            active.add(site)
            key = (int(slot.get("x") or 0), int(slot.get("y") or 0))
            cell = self.tray_slots.get(key)
            if cell is None:
                continue
            cell["state"] = "testing"
            cell["part_id"] = str(slot.get("part_id") or "")
            cell["site"] = site
            self.site_status[site] = "testing"
        for site in data.get("masked_sites") or []:
            number = int(site)
            if number not in active:
                self.site_status[number] = "masked"

    def _pick_slot(self, part: dict[str, Any], info: dict[str, Any]) -> None:
        """Move a finished part off the input tray and into its hard-bin pocket."""
        key = (int(part.get("x") or 0), int(part.get("y") or 0))
        cell = self.tray_slots.get(key)
        if cell is not None:
            cell["state"] = "picked"
            cell["part_id"] = info["part_id"]
            cell["passed"] = info["passed"]
            cell["bin"] = int(part.get("hard_bin") or part.get("soft_bin") or 0)
        hard = int(part.get("hard_bin") or 0)
        previous = self._sorted_bin.get(info["part_id"])
        if previous is not None:
            left = self.output_bins.get(previous, 0) - 1
            if left > 0:
                self.output_bins[previous] = left
            else:
                self.output_bins.pop(previous, None)
        self._sorted_bin[info["part_id"]] = hard
        self.output_bins[hard] = self.output_bins.get(hard, 0) + 1

    def tray_view(self) -> dict[str, Any]:
        """Snapshot the Tray page paints: input grid, sites, output pockets."""
        return {
            "title": self.unit,
            "cols": self.tray_cols,
            "rows": self.tray_rows,
            "total": self.tray_total,
            "tested": sum(1 for cell in self.tray_slots.values() if cell.get("state") == "picked"),
            "jams": self.jams,
            "jam_now": bool(self.touch.get("jam")),
            "slots": [
                {"x": x, "y": y, **cell}
                for (x, y), cell in self.tray_slots.items()
            ],
            "sites": [
                {"site": site, "status": status}
                for site, status in sorted(self.site_status.items())
            ],
            "output": [
                {"bin": hard, "count": count}
                for hard, count in sorted(self.output_bins.items())
                if count
            ],
        }

    def _add_log(self, level: str, message: str) -> None:
        if not message:
            return
        entry = LogEntry(seq=self.total_events, level=level, message=message)
        self._logs.append(entry)
        self._pending_logs.append(entry)
        self._dirty = True

    # ------------------------------------------------------------------ state
    @property
    def yield_pct(self) -> float:
        """Current yield in percent."""
        return 100.0 * self.passed / self.parts if self.parts else 0.0

    @property
    @_synchronized
    def rows(self) -> list[DatalogRow]:
        """Buffered datalog rows (newest last)."""
        return list(self._rows)

    @property
    @_synchronized
    def logs(self) -> list[LogEntry]:
        """Buffered log entries."""
        return list(self._logs)

    @_synchronized
    def bin_rows(self) -> list[tuple[int, int, float]]:
        """``(bin, count, pct)`` rows sorted by count."""
        total = sum(self.bins.values()) or 1
        return [(soft, count, 100.0 * count / total)
                for soft, count in sorted(self.bins.items(), key=lambda kv: (-kv[1], kv[0]))]

    @_synchronized
    def snapshot(self) -> dict[str, Any]:
        """Cheap summary for the stats panel."""
        return {
            "unit": self.unit,
            "parts": self.parts,
            "passed": self.passed,
            "yield_pct": round(self.yield_pct, 3),
            "retests": self.retests,
            "bins": dict(sorted(self.bins.items())),
            "touchdown": self.touchdown,
            "touchdowns": self.touchdowns,
            "uph": round(self.uph, 2),
            "gross_uph": round(float(self.metrics.get("gross_uph") or 0.0), 2),
            "theoretical_uph": round(float(self.metrics.get("theoretical_uph") or 0.0), 2),
            "oee": round(float(self.metrics.get("oee") or 0.0) * 100.0, 2),
            "availability": round(float(self.metrics.get("availability") or 0.0) * 100.0, 2),
            "performance": round(float(self.metrics.get("performance") or 0.0) * 100.0, 2),
            "quality": round(float(self.metrics.get("quality") or 0.0) * 100.0, 2),
            "jams": self.jams,
            "unit_kind": self.unit_kind,
            "running": self.running,
            "finished": self.finished,
            "aborted": self.aborted,
            "paused_at": dict(self.paused_at) if self.paused_at else None,
            "error": self.error,
            "history_groups": len(self._history_keys),
            "history_evictions": self.history_evictions,
        }

    # --------------------------------------------------------------- throttle
    @_synchronized
    def should_render(self, now: float | None = None) -> bool:
        """True when enough time passed since the last render (throttling)."""
        moment = time.monotonic() if now is None else now
        if not self._dirty:
            return False
        if (moment - self._last_render) * 1000.0 < self.throttle_ms:
            return False
        self._last_render = moment
        self._dirty = False
        return True

    @_synchronized
    def take_rows(self) -> list[DatalogRow]:
        """Return the rows added since the previous call."""
        rows = list(self._pending)
        self._pending.clear()
        return rows

    def frames_per_second(self) -> float:
        """Effective render rate implied by ``throttle_ms``."""
        return 1000.0 / self.throttle_ms if self.throttle_ms else 0.0

    def rows_for_site(self, site: int | None) -> Iterable[DatalogRow]:
        """Filter helper used by the datalog panel."""
        if site is None:
            return list(self._rows)
        return [row for row in self._rows if row.site == site]

    def failures(self) -> list[DatalogRow]:
        """All buffered failing rows."""
        return [row for row in self._rows if not row.passed]

    @_synchronized
    def take_logs(self) -> list[LogEntry]:
        """Return the log lines added since the previous call."""
        rows = list(self._pending_logs)
        self._pending_logs.clear()
        return rows

    @_synchronized
    def test_numbers(self) -> list[tuple[int, str]]:
        """Tests seen so far, in numeric order."""
        return [(num, self._test_names.get(num, "")) for num in sorted(self._test_names)]

    @_synchronized
    def trend(self, test_num: int, site: int | None = None,
              wafer: str | None = None, pin: str | None = None) -> list[float]:
        """Recent measured values of one test (oldest first)."""
        if pin is not None:
            return self._values(test_num, site, wafer, pin)
        if wafer:
            return list(self._wafer_series.get((int(test_num), str(wafer)), ()))
        if site is None:
            return list(self._series.get(test_num, ()))
        return list(self._site_series.get((int(test_num), int(site)), ()))

    def histogram(self, test_num: int, bins: int = 10,
                  site: int | None = None,
                  wafer: str | None = None,
                  pin: str | None = None) -> list[tuple[float, float, int]]:
        """Histogram of one test. ``site``, ``wafer`` or ``pin`` limits the sample."""
        return _histogram(self._values(test_num, site, wafer, pin), bins)

    def probability(self, test_num: int, site: int | None = None,
                    wafer: str | None = None,
                    pin: str | None = None) -> list[tuple[float, float]]:
        """Normal probability plot as ``(value, z)`` pairs, sorted by value.

        ``z`` is the inverse-normal of the plotting position ``(i+0.5)/n``.
        Fewer than two numeric samples yields an empty list.
        """
        data = sorted(self._values(test_num, site, wafer, pin))
        count = len(data)
        if count < 2:
            return []
        return [(value, _norm_ppf((index + 0.5) / count)) for index, value in enumerate(data)]

    @_synchronized
    def summary_rows(self, *, by_site: bool = False,
                     group: str | None = None,
                     groups: Iterable[str] | None = None) -> list[TestSummaryRow]:
        """Per-test summary.

        ``groups`` may contain ``site``, ``wafer`` and ``pin`` together.
        ``group="test"`` (the default) is one row per test. ``by_site=True``
        is the same as ``group="site"``.
        """
        selected = _group_names(group, by_site=by_site, groups=groups)
        grouped: dict[tuple[int, int | None, str | None, str | None], _Acc] = {}
        source = self._acc if "pin" in selected else self._totals
        for (test_num, site, wafer, pin), acc in source.items():
            key = (
                test_num,
                site if "site" in selected else None,
                wafer if "wafer" in selected else None,
                pin if "pin" in selected else None,
            )
            bucket = grouped.get(key)
            if bucket is None:
                grouped[key] = _Acc(
                    name=acc.name, count=acc.count, passed=acc.passed,
                    numeric=acc.numeric, total=acc.total, total_sq=acc.total_sq,
                    mean=acc.mean, m2=acc.m2,
                    minimum=acc.minimum, maximum=acc.maximum,
                    lo=acc.lo, hi=acc.hi,
                )
                continue
            bucket.name = acc.name or bucket.name
            bucket.count += acc.count
            bucket.passed += acc.passed
            if acc.numeric:
                combined = bucket.numeric + acc.numeric
                delta = acc.mean - bucket.mean
                bucket.m2 += acc.m2 + delta * delta * bucket.numeric * acc.numeric / combined
                bucket.mean += delta * acc.numeric / combined
            bucket.numeric += acc.numeric
            bucket.total += acc.total
            bucket.total_sq += acc.total_sq
            if acc.minimum is not None:
                bucket.minimum = acc.minimum if bucket.minimum is None else min(bucket.minimum, acc.minimum)
            if acc.maximum is not None:
                bucket.maximum = acc.maximum if bucket.maximum is None else max(bucket.maximum, acc.maximum)
            if acc.lo is not None:
                bucket.lo = acc.lo
            if acc.hi is not None:
                bucket.hi = acc.hi
        rows: list[TestSummaryRow] = []
        for (test_num, site, wafer, pin), acc in grouped.items():
            mean = acc.mean if acc.numeric else None
            stdev = _stdev(acc)
            cp, cpk = _cp_cpk(mean, stdev, acc.lo, acc.hi)
            rows.append(TestSummaryRow(
                test_num=test_num,
                name=acc.name,
                site=site,
                wafer=wafer,
                pin=pin,
                count=acc.count,
                passed=acc.passed,
                failed=acc.count - acc.passed,
                mean=mean,
                stdev=stdev,
                minimum=acc.minimum,
                maximum=acc.maximum,
                lo=acc.lo,
                hi=acc.hi,
                cp=cp,
                cpk=cpk,
            ))
        rows.sort(key=lambda row: (
            row.test_num,
            -1 if row.site is None else row.site,
            "" if row.wafer is None else row.wafer,
            "" if row.pin is None else row.pin,
        ))
        return rows

    @_synchronized
    def _values(self, test_num: int, site: int | None = None,
                wafer: str | None = None, pin: str | None = None) -> list[float]:
        values: list[float] = []
        for (num, sample_site, sample_wafer, sample_pin), samples in self._samples.items():
            if num != int(test_num):
                continue
            if site is not None and sample_site != int(site):
                continue
            if wafer is not None and sample_wafer != str(wafer):
                continue
            if pin is not None and sample_pin != str(pin):
                continue
            values.extend(samples)
        return values

    @_synchronized
    def limits_of(self, test_num: int, site: int | None = None,
                  wafer: str | None = None,
                  pin: str | None = None) -> tuple[float | None, float | None]:
        """Spec limits for the same slice the summary charts are showing."""
        lo: float | None = None
        hi: float | None = None
        source = self._acc if pin is not None else self._totals
        for (num, sample_site, sample_wafer, sample_pin), acc in source.items():
            if num != int(test_num):
                continue
            if site is not None and sample_site != int(site):
                continue
            if wafer is not None and sample_wafer != str(wafer):
                continue
            if pin is not None and sample_pin != str(pin):
                continue
            if acc.lo is not None:
                lo = acc.lo
            if acc.hi is not None:
                hi = acc.hi
        return lo, hi

    def shmoo(self) -> dict[str, Any]:
        """Latest shmoo plot payload (empty when the run has not produced one)."""
        return dict(self._shmoo) if self._shmoo else {}

    def set_shmoo(self, data: dict[str, Any] | None) -> None:
        """Store shmoo results coming from a test item or the API."""
        self._shmoo = normalize_shmoo(data)
        self._dirty = True

    @_synchronized
    def add_measurement(self, *, test_num: int, name: str, site: int,
                        value: float | None, passed: bool, wafer: str = "",
                        part: str = "", pin: str = "",
                        lo: float | None = None, hi: float | None = None) -> None:
        """Record one result that did not come from the live event bus.

        Used when a saved STDF file is loaded into the same summary tables.
        """
        self.unit = wafer or ""
        self._on_test_result(self.total_events + 1, {"result": {
            "test_num": int(test_num),
            "test_name": name,
            "site": int(site),
            "value": value,
            "passed": bool(passed),
            "part_id": part,
            "pin": pin,
            "lo_limit": lo,
            "hi_limit": hi,
        }})


def _accumulate(acc: _Acc, row: DatalogRow) -> None:
    acc.name = row.name or acc.name
    acc.count += 1
    acc.passed += int(row.passed)
    if (isinstance(row.value, (int, float)) and not isinstance(row.value, bool)
            and math.isfinite(row.value)):
        value = float(row.value)
        acc.numeric += 1
        delta = value - acc.mean
        acc.mean += delta / acc.numeric
        acc.m2 += delta * (value - acc.mean)
        acc.total += value
        acc.total_sq += value * value
        acc.minimum = value if acc.minimum is None else min(acc.minimum, value)
        acc.maximum = value if acc.maximum is None else max(acc.maximum, value)
    acc.lo = _spec_limit(row.lo)
    acc.hi = _spec_limit(row.hi)


def _stdev(acc: _Acc) -> float | None:
    """Sample standard deviation. ``None`` when the test has no numeric value."""
    if acc.numeric < 2:
        return None
    variance = acc.m2 / (acc.numeric - 1)
    return math.sqrt(max(0.0, variance))


def _norm_ppf(probability: float) -> float:
    """Approximate inverse of the standard normal CDF (Abramowitz and Stegun 26.2.23)."""
    if probability <= 0.0:
        return -8.0
    if probability >= 1.0:
        return 8.0
    if probability < 0.5:
        return -_norm_ppf(1.0 - probability)
    t = math.sqrt(-2.0 * math.log(1.0 - probability))
    num = 2.515517 + t * (0.802853 + t * 0.010328)
    den = 1.0 + t * (1.432788 + t * (0.189269 + t * 0.001308))
    return t - num / den


def _histogram(values: list[float], bins: int = 10) -> list[tuple[float, float, int]]:
    """``(low, high, count)`` bins. Kept here so the view model does not import datalog."""
    data = sorted(values)
    if not data:
        return []
    count = max(1, int(bins))
    low, high = data[0], data[-1]
    if high == low:
        return [(low, high, len(data))]
    width = (high - low) / count
    buckets = [0] * count
    for value in data:
        index = min(count - 1, int((value - low) / width))
        buckets[index] += 1
    return [(low + index * width, low + (index + 1) * width, hits)
            for index, hits in enumerate(buckets)]
