"""failwatch: a minimal pyate plugin (hook only, no config change needed).

It listens to the item hooks and appends every failing measurement to
``<output_dir>/fails.log``.  Load it without installing anything:

    set PYATE_PLUGIN_PATH=examples/plugins          (cmd)
    $env:PYATE_PLUGIN_PATH = "examples/plugins"     (PowerShell)
    pyate plugin list
    pyate run configs/demo_cp/session.yaml

The same file shows both halves of the plugin contract: ``pyate_register``
(what the plugin contributes) and a runtime hook (``after_item``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyate.plugins.hookspec import API_VERSION, hookimpl

__version__ = "0.1.0"
__all__ = ["Plugin"]


class Plugin:
    """Entry object; pyate discovers it through the ``pyate.plugins`` entry point."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.fails = 0

    @hookimpl
    def pyate_api_version(self) -> str:
        """Plugin API version this plugin was written against."""
        return API_VERSION

    @hookimpl
    def pyate_register(self, registry: Any) -> None:
        """Nothing to register for a hook-only plugin, just announce it."""
        registry.note("failwatch: writes <out>/fails.log for every failing result")

    @hookimpl
    def before_lot(self, run: Any = None, **kwargs: Any) -> None:
        """Create the log at the start of a run."""
        self.fails = 0
        output_dir = Path(getattr(run, "output_dir", "out"))
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = output_dir / "fails.log"
        self.path.write_text("test_num,test_name,site,part,value\n", encoding="utf-8")

    @hookimpl
    def after_item(self, ctx: Any = None, item: Any = None,
                   results: Any = None, **kwargs: Any) -> None:
        """Append one line per failing measurement."""
        if self.path is None:
            return
        for result in results or []:
            if result.passed:
                continue
            self.fails += 1
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{result.test_num},{result.test_name},{result.site},"
                    f"{getattr(ctx, 'part_id', '')},{result.value}\n"
                )

    @hookimpl
    def after_lot(self, summary: Any = None, **kwargs: Any) -> None:
        """Print a one line summary (visible in the CLI / GUI log)."""
        if self.path is None:
            return
        print(f"failwatch: {self.fails} failing result(s) -> {self.path}")
