"""Core data objects shared by engine, scenario, datalog and GUI.

Everything in this module is a plain dataclass: no HAL, no config and no GUI
dependency, so it can be serialised, hashed and shipped across threads
without surprises.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["TestResult", "SiteContext", "PartResult", "RunContext", "TouchdownInfo"]


@dataclass
class TestResult:
    """One measured parameter or functional test for one part/site.

    ``value`` is the raw measured number (``None`` for pure pass/fail tests
    and for tests that could not be executed).  Limit judgement is applied by
    the flow executor, never by the test item itself.
    """

    test_num: int
    test_name: str
    site: int
    value: float | None = None
    unit: str = ""
    lo_limit: float | None = None
    hi_limit: float | None = None
    passed: bool = True
    fail_code: int = 0
    pin: str | None = None
    kind: str = "PTR"  # PTR | FTR | MPR
    skipped: bool = False
    # functional-test bookkeeping (FTR)
    cycle_cnt: int = 0
    rel_vadr: int = 0
    num_fail: int = 0
    fail_pins: tuple[str, ...] = ()
    vector_name: str = ""
    test_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ api
    def judge(self, lo: float | None = None, hi: float | None = None) -> "TestResult":
        """Apply limits in place (``None`` means unbounded)."""
        self.lo_limit = lo
        self.hi_limit = hi
        if self.skipped:
            return self
        if self.value is None:
            # A parameter test without a value can only pass when it carries an
            # explicit functional verdict.
            self.passed = bool(self.extra.get("force_pass", False))
            return self
        self.passed = (lo is None or self.value >= lo) and (hi is None or self.value <= hi)
        return self

    def fail(self, code: int = 1) -> "TestResult":
        """Force a fail verdict with an optional fail code."""
        self.passed = False
        self.fail_code = code
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "test_num": self.test_num,
            "test_name": self.test_name,
            "site": self.site,
            "value": self.value,
            "unit": self.unit,
            "lo_limit": self.lo_limit,
            "hi_limit": self.hi_limit,
            "passed": self.passed,
            "fail_code": self.fail_code,
            "pin": self.pin,
            "kind": self.kind,
            "skipped": self.skipped,
            "cycle_cnt": self.cycle_cnt,
            "rel_vadr": self.rel_vadr,
            "num_fail": self.num_fail,
            "fail_pins": list(self.fail_pins),
            "vector_name": self.vector_name,
            "test_time_ms": self.test_time_ms,
            "timestamp": self.timestamp,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestResult":
        """Inverse of :meth:`to_dict`."""
        payload = dict(data)
        payload["fail_pins"] = tuple(payload.get("fail_pins") or ())
        return cls(**payload)


@dataclass
class SiteContext:
    """Per-site state for one touchdown / one part."""

    site_id: int
    dut: Any = None
    pin_map: dict[str, int] = field(default_factory=dict)
    results: list[TestResult] = field(default_factory=list)
    soft_bin: int | None = None
    hard_bin: int | None = None
    fail_code: int = 0
    stopped: bool = False
    skipped: bool = False
    x_coord: int = 0
    y_coord: int = 0
    part_id: str = ""
    touch_id: int = 0
    test_time_ms: float = 0.0
    retest: bool = False
    vars: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ api
    def new_part(self, *, part_id: str = "", x: int = 0, y: int = 0, retest: bool = False) -> None:
        """Reset state before testing a new part on this site."""
        self.results.clear()
        self.soft_bin = None
        self.hard_bin = None
        self.fail_code = 0
        self.stopped = False
        self.skipped = False
        self.test_time_ms = 0.0
        self.part_id = part_id
        self.x_coord = x
        self.y_coord = y
        self.retest = retest
        self.vars = {}

    def add_result(self, result: TestResult) -> TestResult:
        """Append a result and mirror a fail into the site fail code."""
        self.results.append(result)
        if not result.passed and not result.skipped and not self.fail_code:
            self.fail_code = result.fail_code or 1
        return result

    @property
    def failed(self) -> bool:
        """True when at least one executed test failed."""
        return any((not r.passed) and not r.skipped for r in self.results)

    def first_fail(self) -> TestResult | None:
        """First failing result, if any."""
        for result in self.results:
            if not result.passed and not result.skipped:
                return result
        return None

    def result_of(self, test_name: str) -> TestResult | None:
        """Look up the last result produced by ``test_name``."""
        for result in reversed(self.results):
            if result.test_name == test_name:
                return result
        return None

    def value_of(self, test_name: str) -> float | None:
        """Measured value of ``test_name`` (``None`` when absent)."""
        result = self.result_of(test_name)
        return None if result is None else result.value


@dataclass
class PartResult:
    """Final verdict for one tested part."""

    part_id: str
    site: int
    x: int = 0
    y: int = 0
    soft_bin: int = 0
    hard_bin: int = 0
    passed: bool = False
    fail_code: int = 0
    num_tests: int = 0
    test_time_ms: float = 0.0
    retest: bool = False
    skipped: bool = False
    results: list[TestResult] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_results: bool = False) -> dict[str, Any]:
        """Serialise (results optionally included)."""
        data: dict[str, Any] = {
            "part_id": self.part_id,
            "site": self.site,
            "x": self.x,
            "y": self.y,
            "soft_bin": self.soft_bin,
            "hard_bin": self.hard_bin,
            "passed": self.passed,
            "fail_code": self.fail_code,
            "num_tests": self.num_tests,
            "test_time_ms": self.test_time_ms,
            "retest": self.retest,
            "skipped": self.skipped,
            "extra": dict(self.extra),
        }
        if include_results:
            data["results"] = [r.to_dict() for r in self.results]
        return data


@dataclass(frozen=True)
class TouchdownInfo:
    """Geometry of one touchdown (prober/handler index)."""

    touch_id: int
    unit_id: str
    sites: tuple[tuple[int, int, int, str], ...] = ()
    """``(site_id, x, y, part_id)`` for every active site of the touchdown."""

    index_time_ms: float = 0.0
    xtra: dict[str, Any] = field(default_factory=dict)

    def active_sites(self) -> list[int]:
        """Site ids that carry a die in this touchdown."""
        return [entry[0] for entry in self.sites]


@dataclass
class RunContext:
    """Run-wide metadata: lot, unit, stage, program and live site contexts."""

    lot_id: str = ""
    wafer_id: str = ""
    sublot: str = ""
    stage: str = "CP1"
    program_name: str = ""
    program_revision: str = ""
    operator: str = "PYATE"
    temperature: float = 25.0
    seed: int = 0
    sites: list[SiteContext] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def site(self, site_id: int) -> SiteContext:
        """Return the context of ``site_id`` (1-based)."""
        for ctx in self.sites:
            if ctx.site_id == site_id:
                return ctx
        raise KeyError(f"site {site_id} not present in run context")

    def new_parts(self, parts: Iterable[Any]) -> None:
        """Reset every site context for a new touchdown.

        ``parts`` yields :class:`TouchdownInfo`-like tuples via ``sites``.
        """
        for entry in parts:
            site_id, x, y, part_id = entry
            self.site(site_id).new_part(part_id=part_id, x=x, y=y)

    @property
    def elapsed_s(self) -> float:
        """Wall clock duration of the run."""
        end = self.finished_at or time.time()
        return end - self.started_at
