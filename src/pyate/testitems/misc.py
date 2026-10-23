"""Utility items: delays and user Python callbacks."""

from __future__ import annotations

import importlib
from typing import Any, Callable

from ..engine.context import SiteContext
from ..hal.base import HALBackend
from .base import TestItem, TestItemParams
from .registry import register_testitem

__all__ = ["DelayParams", "DelayItem", "UserCodeParams", "UserCodeItem"]


class DelayParams(TestItemParams):
    """Delay parameters."""

    ms: float = 1.0
    lo: float | None = None
    hi: float | None = None


@register_testitem("delay_ms")
class DelayItem(TestItem):
    """Waits ``ms`` milliseconds of simulated (or real) time."""

    name = "delay_ms"
    Params = DelayParams
    description = "Programmable delay"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[Any]:
        hal.add_time(ctx.site_id, self.params.ms)
        lo, hi = self.default_limits()
        return [self.make_result(ctx, name="delay_ms", value=self.params.ms, unit="ms",
                                 lo=lo, hi=hi)]


class UserCodeParams(TestItemParams):
    """User callback parameters."""

    callable: str
    args: dict[str, Any] = {}
    lo: float | None = None
    hi: float | None = None


@register_testitem("user_code")
class UserCodeItem(TestItem):
    """Calls ``module.function(ctx, hal, **args)`` and records its value."""

    name = "user_code"
    Params = UserCodeParams
    description = "User supplied Python callback"

    def execute(self, ctx: SiteContext, hal: HALBackend) -> list[Any]:
        target = self._resolve(self.params.callable)
        value = target(ctx, hal, **self.params.args)
        lo, hi = self.default_limits()
        result = self.make_result(ctx, name=f"user_{self.params.callable.rsplit('.', 1)[-1]}",
                                  value=float(value) if value is not None else None,
                                  lo=lo, hi=hi)
        return [result]

    @staticmethod
    def _resolve(path: str) -> Callable[..., Any]:
        module_name, _, attribute = path.rpartition(".")
        if not module_name:
            raise ValueError(f"callable must be 'module.function', got '{path}'")
        module = importlib.import_module(module_name)
        func = getattr(module, attribute, None)
        if not callable(func):
            raise ValueError(f"'{path}' is not callable")
        return func
