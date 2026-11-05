"""Reference plugin #2: a JSON datalog sink.

The sink writes one ``<unit>.jsonl`` file per wafer/tray with one JSON object per
part, which is handy for feeding dashboards or databases.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pyate.engine.context import PartResult
from pyate.plugins.hookspec import API_VERSION, hookimpl

__version__ = "0.1.0"
__all__ = ["JsonSink", "Plugin"]


class JsonSink:
    """Writes ``<output_dir>/<unit>.jsonl`` with one line per part."""

    def __init__(self, output_dir: str | Path = "out", *, site_count: int = 1,
                 compact: bool = True) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.site_count = site_count
        self.compact = compact
        self.path: Path | None = None
        self.files: list[Path] = []
        self._handle: Any = None
        self.parts = 0

    # ------------------------------------------------------------------ hooks
    def on_unit_start(self, unit: Any) -> None:
        """Open the file of a new unit."""
        self.path = self.output_dir / f"{unit.unit_id}.jsonl"
        self._handle = self.path.open("w", encoding="utf-8")
        self.files.append(self.path)

    def on_part(self, part: PartResult, unit: Any = None) -> None:
        """Append one part as a JSON line."""
        if self._handle is None or part.skipped:
            return
        record = {
            "unit": getattr(unit, "unit_id", ""),
            "part_id": part.part_id,
            "site": part.site,
            "x": part.x,
            "y": part.y,
            "hard_bin": part.hard_bin,
            "soft_bin": part.soft_bin,
            "passed": part.passed,
            "retest": part.retest,
            "test_time_ms": round(part.test_time_ms, 3),
            "tests": [
                {
                    "test_num": result.test_num,
                    "name": result.test_name,
                    "value": result.value,
                    "unit": result.unit,
                    "lo": result.lo_limit,
                    "hi": result.hi_limit,
                    "passed": result.passed,
                }
                for result in part.results
                if not result.skipped
            ],
        }
        self._handle.write(json.dumps(record, separators=(",", ":") if self.compact else None))
        self._handle.write("\n")
        self.parts += 1

    def on_unit_end(self, summary: Any) -> None:
        """Close the current unit file."""
        self.close()

    def on_run_end(self, summary: Any) -> None:
        """Close any remaining file."""
        self.close()

    def close(self) -> None:
        """Flush and close the open file."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class Plugin:
    """Entry object referenced by the ``pyate.plugins`` entry point."""

    @hookimpl
    def pyate_api_version(self) -> str:
        """Plugin API version this plugin targets."""
        return API_VERSION

    @hookimpl
    def pyate_register(self, registry: Any) -> None:
        """Expose the sink factory to the kernel and to other tools."""
        registry.add_sink("json", lambda **kwargs: JsonSink(**kwargs))
        registry.note("plugin_json_sink: 'json' datalog sink")

    @hookimpl
    def after_lot(self, summary: Any = None, **kwargs: Any) -> None:
        """Write a tiny sidecar with the run outcome next to the sink files."""
        out = kwargs.get("output_dir")
        if not out or summary is None:
            return
        data = summary.to_dict() if hasattr(summary, "to_dict") else {}
        path = Path(out) / "plugin_json_sink.json"
        path.write_text(json.dumps({"written_at": time.time(), "summary": data}, indent=2),
                        encoding="utf-8")
