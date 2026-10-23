"""TestItem base class, parameter schemas and the limit lookup service."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import Limit, LimitsConfig
from ..engine.context import SiteContext, TestResult
from ..hal.base import HALBackend

__all__ = ["TestItemParams", "TestItem", "LimitsTable", "PatternCache"]

#: ``TestResult`` fields that ``make_result`` forwards instead of storing in ``extra``.
_RESULT_FIELDS = frozenset({
    "passed", "skipped", "cycle_cnt", "rel_vadr", "num_fail", "fail_pins",
    "vector_name", "test_time_ms",
})


class TestItemParams(BaseModel):
    """Base class for every item's parameter schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fail_code: int = Field(default=0, description="fail code reported when this item fails")


class LimitsTable:
    """Resolves the limit row for ``test_num`` + stage + temperature."""

    def __init__(self, limits: LimitsConfig | None = None) -> None:
        self._rows: dict[int, list[Limit]] = {}
        for row in (limits.limits if limits else []):
            self._rows.setdefault(row.test_num, []).append(row)

    @classmethod
    def from_config(cls, limits: LimitsConfig | None) -> "LimitsTable":
        """Build from a loaded limits config."""
        return cls(limits)

    def lookup(self, test_num: int, stage: str = "*", temperature: float = 25.0) -> Limit | None:
        """Most specific matching row (stage exact > ``*``; narrowest temperature)."""
        candidates = [row for row in self._rows.get(test_num, []) if row.matches(stage, temperature)]
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row.stage != "*", row.temp_max - row.temp_min))
        return candidates[0]

    def all_for(self, test_num: int) -> list[Limit]:
        """Every row defined for ``test_num``."""
        return list(self._rows.get(test_num, []))

    def __len__(self) -> int:
        return sum(len(rows) for rows in self._rows.values())


class PatternCache:
    """Loads and caches ``.pat`` files for one run."""

    def __init__(self, resolver: Any = None) -> None:
        self._resolver = resolver
        self._cache: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        """Return a parsed pattern (loading it on first use)."""
        from ..pattern.parser import load_pattern

        if name in self._cache:
            return self._cache[name]
        if "\n" in name or name.strip().startswith("pattern "):
            pattern = load_pattern(name)
        else:
            path = self._resolver(name) if self._resolver else None
            pattern = load_pattern(str(path or name))
        self._cache[name] = pattern
        return pattern

    def clear(self) -> None:
        """Drop the cache."""
        self._cache.clear()


class TestItem(ABC):
    """Base class for all test items.

    ``execute`` must return raw measurements; limit judgement is applied by
    the flow executor through :class:`LimitsTable`.
    """

    name: ClassVar[str] = "testitem"
    Params: ClassVar[type[TestItemParams]] = TestItemParams
    description: ClassVar[str] = ""

    def __init__(self, test_num: int, params: dict[str, Any] | None = None,
                 *, defn_name: str | None = None) -> None:
        self.test_num = int(test_num)
        self.params: Any = self.Params.model_validate(params or {})
        self.defn_name = defn_name or self.name

    # ------------------------------------------------------------------ hooks
    def setup(self, ctx: SiteContext, hal: HALBackend) -> None:
        """Prepare instruments before ``execute`` (optional)."""

    @abstractmethod
    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[TestResult]:
        """Run the item and return one or more raw results."""

    def cleanup(self, ctx: SiteContext, hal: HALBackend) -> None:
        """Release instruments after ``execute`` (always called)."""

    # ---------------------------------------------------------------- helpers
    def make_result(
        self,
        ctx: SiteContext,
        *,
        test_num: int | None = None,
        name: str | None = None,
        value: float | None = None,
        unit: str = "",
        lo: float | None = None,
        hi: float | None = None,
        pin: str | None = None,
        kind: str = "PTR",
        **extra: Any,
    ) -> TestResult:
        """Create a result pre-filled with this item's fail code and site.

        Keyword arguments that name a :class:`TestResult` field (``cycle_cnt``,
        ``num_fail``, ``fail_pins``, ...) are routed to the field itself so that
        the STDF FTR record sees the real values; anything else is kept in
        ``extra`` for the datalog sinks.
        """
        fail_code = int(getattr(self.params, "fail_code", 0) or 0)
        fields: dict[str, Any] = {}
        for key in _RESULT_FIELDS:
            if key in extra:
                fields[key] = extra.pop(key)
        if "fail_pins" in fields:
            fields["fail_pins"] = tuple(fields["fail_pins"] or ())
        result = TestResult(
            test_num=self.test_num if test_num is None else test_num,
            test_name=name or self.defn_name,
            site=ctx.site_id,
            value=value,
            unit=unit,
            lo_limit=lo,
            hi_limit=hi,
            fail_code=fail_code,
            pin=pin,
            kind=kind,
            extra=extra,
            **fields,
        )
        return result

    def default_limits(self) -> tuple[float | None, float | None]:
        """Item level default limits (overridden by ``limits.csv``)."""
        return (
            getattr(self.params, "lo", None),
            getattr(self.params, "hi", None),
        )

    def resource(self, hal: HALBackend, ctx: SiteContext) -> Any:
        """Shortcut to the site resources."""
        return hal.site(ctx.site_id)

    def noise(self, hal: HALBackend, site_id: int, value: float,
              sigma: float = 0.005) -> float:
        """Apply backend noise when available (identity for real HALs)."""
        func = getattr(hal, "apply_noise", None)
        return func(site_id, value, sigma) if func else float(value)

    @property
    def pins(self) -> list[str]:
        """Explicit pin list from params (empty means 'all DUT pins')."""
        return list(getattr(self.params, "pins", []) or [])
