"""pluggy hook specifications for pyate plugins.

Every hook is optional: a plugin only implements the ones it needs. Hook arguments
are passed as keywords, so implementation signatures must use keyword arguments.
"""

from __future__ import annotations

from typing import Any

import pluggy

PROJECT_NAME = "pyate"
API_VERSION = "1.0"

hookspec = pluggy.HookspecMarker(PROJECT_NAME)
hookimpl = pluggy.HookimplMarker(PROJECT_NAME)

__all__ = ["PROJECT_NAME", "API_VERSION", "hookspec", "hookimpl", "PyateSpec"]


class PyateSpec:
    """Extension points offered to plugins."""

    @hookspec
    def pyate_api_version(self) -> str:
        """Return the plugin's supported pyate plugin API version (e.g. ``"1.0"``)."""

    @hookspec
    def pyate_register(self, registry: Any) -> None:
        """Register new test items, DUT models, sinks, instruments or scenarios."""

    @hookspec
    def before_lot(self, run: Any = None, **kwargs: Any) -> None:
        """Called once before the first unit of a run."""

    @hookspec
    def before_unit(self, unit: Any = None, **kwargs: Any) -> None:
        """Called before a wafer / tray starts."""

    @hookspec
    def after_unit(self, summary: Any = None, **kwargs: Any) -> None:
        """Called after a wafer / tray finished."""

    @hookspec
    def after_lot(self, summary: Any = None, **kwargs: Any) -> None:
        """Called after the last unit of a run."""

    @hookspec
    def before_part(self, ctx: Any = None, run_ctx: Any = None, **kwargs: Any) -> None:
        """Called for every part before its first test item runs."""

    @hookspec
    def after_part(self, ctx: Any = None, part: Any = None, **kwargs: Any) -> None:
        """Called for every part after binning."""

    @hookspec
    def before_item(self, ctx: Any = None, item: Any = None, **kwargs: Any) -> None:
        """Called before one test item executes."""

    @hookspec
    def after_item(self, ctx: Any = None, item: Any = None, results: Any = None,
                   **kwargs: Any) -> None:
        """Called after one test item executed (results may be modified)."""

    @hookspec
    def on_fail(self, ctx: Any = None, item: Any = None, results: Any = None,
                **kwargs: Any) -> None:
        """Called when a test item produced failing results."""
