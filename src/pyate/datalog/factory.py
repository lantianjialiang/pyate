"""Datalog sink factory shared by the CLI, the API, the batch runner and the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .csv_log import CsvSink, TextDatalogSink
from .stats import StatsSink
from .stdf.writer import StdfSink

__all__ = ["build_sinks"]


def build_sinks(
    session: Any,
    *,
    stage: str,
    output_dir: str | Path | None = None,
    site_count: int | None = None,
    stdf: bool = True,
    stats: bool = True,
) -> list[Any]:
    """Create the datalog sinks requested by the session's ``stdf`` section.

    The returned list is safe to hand to :class:`~pyate.engine.controller.RunController`:
    every sink implements only the hooks it needs.
    """
    out = Path(output_dir) if output_dir is not None else session.output_dir
    out.mkdir(parents=True, exist_ok=True)
    options = getattr(session, "stdf", None)
    sinks: list[Any] = []
    if stdf:
        sinks.append(StdfSink(
            output_dir=out,
            session=session,
            stage=stage,
            byte_order=getattr(options, "byte_order", "little"),
            gzip_output=bool(getattr(options, "gzip", False)),
            write_wcr=bool(getattr(options, "write_wcr", False)),
            write_fail_pin=True,
            site_count=int(site_count or session.site_count()),
        ))
    if getattr(options, "write_csv", True):
        sinks.append(CsvSink(out))
    if getattr(options, "write_datalog", True):
        sinks.append(TextDatalogSink(out))
    if stats:
        sinks.append(StatsSink(out / "stats.json"))
    return sinks
