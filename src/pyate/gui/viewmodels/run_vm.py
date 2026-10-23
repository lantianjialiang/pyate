"""Qt-free view models for the GUI (unit testable in headless mode).

The view models subscribe to the :class:`~pyate.engine.events.EventBus` and keep a
bounded, throttled snapshot of the run state. The Qt layer only renders what the
view model exposes, which keeps the 10k-events-per-second case smooth: rendering
happens at most once per ``throttle_ms``.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from ...api import Event, EventBus, EventKind, normalize_shmoo

__all__ = ["DatalogRow", "LogEntry", "RunViewModel", "TestSummaryRow", "DIFFICULTY_BINS"]


@dataclass(frozen=True)
class DatalogRow:
    """One measured value, ready for a table model."""

    seq: int
    site: int
    test_num: int
    name: str
    value: float | None
    unit: str
    lo: float | None
    hi: float | None
    passed: bool

    def as_tuple(self) -> tuple[Any, ...]:
        """Row content in display order."""
        return (self.site, self.test_num, self.name, self.value, self.unit,
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

    ``site`` is ``None`` when the row combines every site.
    """

    test_num: int
    name: str
    site: int | None
    count: int
    passed: int
    failed: int
    mean: float | None
    stdev: float | None
    minimum: float | None
    maximum: float | None


@dataclass
class _Acc:
    """Running count for one ``(test_num, site)`` pair."""

    name: str
    count: int = 0
    passed: int = 0
    numeric: int = 0
    total: float = 0.0
    total_sq: float = 0.0
    minimum: float | None = None
    maximum: float | None = None


#: Soft bins that count as failing (used for the yield preview before binning).
DIFFICULTY_BINS = (2, 3, 4, 5, 6, 7, 8, 9)


class RunViewModel:
    """Aggregates run events into a render friendly snapshot."""

    def __init__(self, *, max_rows: int = 500, max_logs: int = 500,
                 throttle_ms: int = 50) -> None:
        self.max_rows = int(max_rows)
        self.max_logs = int(max_logs)
        self.throttle_ms = int(throttle_ms)
        self.events: EventBus | None = None
        self.reset()

    # ------------------------------------------------------------------ setup
    def attach(self, events: EventBus) -> "RunViewModel":
        """Subscribe to every interesting topic of ``events``."""
        self.events = events
        for kind in (EventKind.TEST_RESULT, EventKind.PART_END, EventKind.BIN_RESULT,
                     EventKind.PROGRESS, EventKind.UNIT_START, EventKind.UNIT_END,
                     EventKind.LOT_START, EventKind.LOT_END, EventKind.LOG,
                     EventKind.ERROR, EventKind.TOUCHDOWN):
            events.subscribe(kind, self.on_event)
        return self

    def detach(self) -> None:
        """Remove every subscription again."""
        if self.events is None:
            return
        for kind in (EventKind.TEST_RESULT, EventKind.PART_END, EventKind.BIN_RESULT,
                     EventKind.PROGRESS, EventKind.UNIT_START, EventKind.UNIT_END,
                     EventKind.LOT_START, EventKind.LOT_END, EventKind.LOG,
                     EventKind.ERROR, EventKind.TOUCHDOWN):
            self.events.unsubscribe(kind, self.on_event)
        self.events = None

    def reset(self) -> None:
        """Drop every collected value (start of a new run)."""
        self._rows: deque[DatalogRow] = deque(maxlen=self.max_rows)
        self._pending: list[DatalogRow] = []
        self._logs: deque[LogEntry] = deque(maxlen=self.max_logs)
        self._pending_logs: list[LogEntry] = []
        self._series: dict[int, deque[float]] = {}
        self._site_series: dict[tuple[int, int], deque[float]] = {}
        self._samples: dict[tuple[int, int], list[float]] = {}
        self._acc: dict[tuple[int, int], _Acc] = {}
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
        self.touch: dict[str, Any] = {}
        self.unit = ""
        self.unit_kind = ""
        self.touchdown = 0
        self.touchdowns = 0
        self.running = False
        self.finished = False
        self.aborted = False
        self.error = ""
        self.last_event_seq = 0
        self._dirty = True
        self._last_render = 0.0
        self.total_events = 0

    # ----------------------------------------------------------------- events
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
            self.running = True
        elif kind == EventKind.UNIT_END.value:
            self._dirty = True
        elif kind == EventKind.TOUCHDOWN.value:
            self.touchdown = int(data.get("touchdown", 0) or 0)
            self.touch = dict(data)
            if data.get("kind"):
                self.unit_kind = str(data.get("kind"))
            self._dirty = True
        elif kind == EventKind.PROGRESS.value:
            self.touchdowns = int(data.get("touchdowns", self.touchdowns) or 0)
            if data.get("uph") is not None:
                self.uph = float(data.get("uph") or 0.0)
            self._dirty = True
        elif kind == EventKind.LOT_START.value:
            self.running = True
            self.finished = False
        elif kind == EventKind.LOT_END.value:
            self.running = False
            self.finished = True
            self.aborted = bool(data.get("aborted", False))
        elif kind == EventKind.LOG.value:
            self._add_log("INFO", str(data.get("message", "")))
        elif kind == EventKind.ERROR.value:
            message = str(data.get("error") or data.get("message") or "")
            self._add_log("ERROR", message)
            self.error = message

    def _on_test_result(self, seq: int, data: dict[str, Any]) -> None:
        result = data.get("result") or {}
        if result.get("skipped"):
            return
        row = DatalogRow(
            seq=seq,
            site=int(result.get("site") or data.get("site") or 0),
            test_num=int(result.get("test_num") or 0),
            name=str(result.get("test_name") or data.get("item") or ""),
            value=result.get("value"),
            unit=str(result.get("unit") or ""),
            lo=result.get("lo_limit"),
            hi=result.get("hi_limit"),
            passed=bool(result.get("passed", True)),
        )
        self._rows.append(row)
        self._pending.append(row)
        self._test_names[row.test_num] = row.name
        acc = self._acc.setdefault((row.test_num, row.site), _Acc(name=row.name))
        acc.name = row.name or acc.name
        acc.count += 1
        if row.passed:
            acc.passed += 1
        if isinstance(row.value, (int, float)) and not isinstance(row.value, bool):
            value = float(row.value)
            self._series.setdefault(row.test_num, deque(maxlen=80)).append(value)
            self._site_series.setdefault((row.test_num, row.site), deque(maxlen=80)).append(value)
            samples = self._samples.setdefault((row.test_num, row.site), [])
            if len(samples) < 4000:
                samples.append(value)
            acc.numeric += 1
            acc.total += value
            acc.total_sq += value * value
            acc.minimum = value if acc.minimum is None else min(acc.minimum, value)
            acc.maximum = value if acc.maximum is None else max(acc.maximum, value)
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
        if self.unit_kind == "wafer":
            coord = (int(part.get("x") or 0), int(part.get("y") or 0))
            self.dies[coord] = soft
            self.die_info[coord] = info
        self._dirty = True

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
    def rows(self) -> list[DatalogRow]:
        """Buffered datalog rows (newest last)."""
        return list(self._rows)

    @property
    def logs(self) -> list[LogEntry]:
        """Buffered log entries."""
        return list(self._logs)

    def bin_rows(self) -> list[tuple[int, int, float]]:
        """``(bin, count, pct)`` rows sorted by count."""
        total = sum(self.bins.values()) or 1
        return [(soft, count, 100.0 * count / total)
                for soft, count in sorted(self.bins.items(), key=lambda kv: (-kv[1], kv[0]))]

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
            "unit_kind": self.unit_kind,
            "running": self.running,
            "finished": self.finished,
            "aborted": self.aborted,
            "error": self.error,
        }

    # --------------------------------------------------------------- throttle
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

    def take_rows(self) -> list[DatalogRow]:
        """Return the rows added since the previous call."""
        rows, self._pending = self._pending, []
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

    def take_logs(self) -> list[LogEntry]:
        """Return the log lines added since the previous call."""
        rows, self._pending_logs = self._pending_logs, []
        return rows

    def test_numbers(self) -> list[tuple[int, str]]:
        """Tests seen so far, in numeric order."""
        return [(num, self._test_names.get(num, "")) for num in sorted(self._test_names)]

    def trend(self, test_num: int, site: int | None = None) -> list[float]:
        """Recent measured values of one test (oldest first).

        ``site`` limits the series to that site. ``None`` keeps the mixed series.
        """
        if site is None:
            return list(self._series.get(test_num, ()))
        return list(self._site_series.get((int(test_num), int(site)), ()))

    def histogram(self, test_num: int, bins: int = 10,
                  site: int | None = None) -> list[tuple[float, float, int]]:
        """Histogram of one test. ``site`` limits the sample to that site."""
        return _histogram(self._values(test_num, site), bins)

    def probability(self, test_num: int, site: int | None = None) -> list[tuple[float, float]]:
        """Normal probability plot as ``(value, z)`` pairs, sorted by value.

        ``z`` is the inverse-normal of the plotting position ``(i+0.5)/n``.
        Fewer than two numeric samples yields an empty list.
        """
        data = sorted(self._values(test_num, site))
        count = len(data)
        if count < 2:
            return []
        return [(value, _norm_ppf((index + 0.5) / count)) for index, value in enumerate(data)]

    def summary_rows(self, *, by_site: bool = False) -> list[TestSummaryRow]:
        """Per-test summary. ``by_site`` splits each test into one row per site."""
        grouped: dict[tuple[int, int | None], _Acc] = {}
        for (test_num, site), acc in self._acc.items():
            key = (test_num, site if by_site else None)
            bucket = grouped.get(key)
            if bucket is None:
                grouped[key] = _Acc(
                    name=acc.name, count=acc.count, passed=acc.passed,
                    numeric=acc.numeric, total=acc.total, total_sq=acc.total_sq,
                    minimum=acc.minimum, maximum=acc.maximum,
                )
                continue
            bucket.name = acc.name or bucket.name
            bucket.count += acc.count
            bucket.passed += acc.passed
            bucket.numeric += acc.numeric
            bucket.total += acc.total
            bucket.total_sq += acc.total_sq
            if acc.minimum is not None:
                bucket.minimum = acc.minimum if bucket.minimum is None else min(bucket.minimum, acc.minimum)
            if acc.maximum is not None:
                bucket.maximum = acc.maximum if bucket.maximum is None else max(bucket.maximum, acc.maximum)
        rows: list[TestSummaryRow] = []
        for (test_num, site), acc in grouped.items():
            mean = (acc.total / acc.numeric) if acc.numeric else None
            rows.append(TestSummaryRow(
                test_num=test_num,
                name=acc.name,
                site=site,
                count=acc.count,
                passed=acc.passed,
                failed=acc.count - acc.passed,
                mean=mean,
                stdev=_stdev(acc),
                minimum=acc.minimum,
                maximum=acc.maximum,
            ))
        rows.sort(key=lambda row: (row.test_num, -1 if row.site is None else row.site))
        return rows

    def _values(self, test_num: int, site: int | None) -> list[float]:
        if site is None:
            values: list[float] = []
            for (num, _site), samples in self._samples.items():
                if num == int(test_num):
                    values.extend(samples)
            return values
        return list(self._samples.get((int(test_num), int(site)), ()))

    def shmoo(self) -> dict[str, Any]:
        """Latest shmoo plot payload (empty when the run has not produced one)."""
        return dict(self._shmoo) if self._shmoo else {}

    def set_shmoo(self, data: dict[str, Any] | None) -> None:
        """Store shmoo results coming from a test item or the API."""
        self._shmoo = normalize_shmoo(data)
        self._dirty = True


def _stdev(acc: _Acc) -> float | None:
    """Sample standard deviation. ``None`` when the test has no numeric value."""
    if acc.numeric < 1:
        return None
    if acc.numeric == 1:
        return 0.0
    mean = acc.total / acc.numeric
    variance = (acc.total_sq - acc.numeric * mean * mean) / (acc.numeric - 1)
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
