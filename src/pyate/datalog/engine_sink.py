"""Re-export the sink base class without importing the engine controller lazily."""

from __future__ import annotations

from ..engine.controller import DatalogSink

__all__ = ["DatalogSink"]
