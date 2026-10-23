"""One-shot shmoo runs and a single payload shape for the GUI plot.

The test item stores ``vdd_axis`` / ``period_axis`` / ``matrix``. The plot
speaks ``x`` (period) / ``y`` (VDD) / ``pass``. :func:`normalize_shmoo` is the
only place that translates between them.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Mapping

from .session import Session

__all__ = ["normalize_shmoo", "shmoo_csv", "run_shmoo"]


def normalize_shmoo(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``{x, y, pass, pass_rate, x_label, y_label}``.

    ``x`` is the period axis in nanoseconds, ``y`` is VDD in volts, and
    ``pass[row][col]`` lines up with ``y[row]`` and ``x[col]``. Accepts either
    that shape or the raw test-item payload.
    """
    raw = dict(data or {})
    ready = raw.get("pass") is not None and "x" in raw and "y" in raw
    if ready:
        x = [float(value) for value in raw.get("x") or []]
        y = [float(value) for value in raw.get("y") or []]
        source = raw.get("pass") or []
    else:
        y = [float(value) for value in (raw.get("y") or raw.get("vdd_axis") or [])]
        x = [float(value) for value in (raw.get("x") or raw.get("period_axis") or [])]
        source = raw.get("matrix") if raw.get("pass") is None else raw.get("pass")
        source = source or []
    matrix = [[bool(cell) for cell in row] for row in source]
    total = sum(len(row) for row in matrix)
    hits = sum(1 for row in matrix for cell in row if cell)
    if raw.get("pass_rate") is None:
        rate = (100.0 * hits / total) if total else 0.0
    else:
        rate = float(raw["pass_rate"])
    return {
        "x": x,
        "y": y,
        "pass": matrix,
        "pass_rate": rate,
        "x_label": str(raw.get("x_label") or "period_ns"),
        "y_label": str(raw.get("y_label") or "VDD"),
    }


def shmoo_csv(data: Mapping[str, Any] | None) -> str:
    """Render a normalized (or raw) shmoo as a CSV matrix."""
    normal = normalize_shmoo(data)
    buffer = io.StringIO()
    # ``\n`` only: a text-mode write on Windows would otherwise turn ``\r\n``
    # into a blank line between every row.
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([f"{normal['y_label']}\\{normal['x_label']}", *normal["x"]])
    for y_value, row in zip(normal["y"], normal["pass"]):
        writer.writerow([y_value, *(1 if cell else 0 for cell in row)])
    return buffer.getvalue()


def run_shmoo(
    session: Session,
    *,
    site: int = 1,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the shmoo item once on ``site`` and return a plot payload.

    Uses the session DUT, levels and pattern files. It does not walk the wafer
    or the handler, so a characterization plot stays cheap.
    """
    from ..config.validators import resolve_pattern_path
    from ..dut.registry import create_dut
    from ..engine.context import SiteContext
    from ..hal.simulated import SimulatedHAL
    from ..pattern.parser import load_pattern
    from ..testitems.registry import create_testitem

    loaded = session.load()
    hal = SimulatedHAL.from_session(loaded, site_count=max(1, int(site)))
    dut = create_dut(loaded.dut_profile, seed=int(loaded.run.seed), sample_yield=False)
    hal.bind_dut(int(site), dut)
    vdd = float(loaded.levels_for().vdd.nom)
    supply = hal.site(int(site)).dps
    supply.set_voltage("VDD", vdd)
    supply.connect("VDD")
    context = SiteContext(site_id=int(site), dut=dut, pin_map=hal.pin_map(int(site)))

    def loader(name: str) -> Any:
        path = resolve_pattern_path(loaded, name)
        if path is None:
            raise FileNotFoundError(f"pattern '{name}' not found")
        return load_pattern(str(path))

    context.extra["pattern_loader"] = loader
    item = create_testitem("shmoo", 9000, dict(params or {}))
    item.setup(context, hal)
    try:
        results = item.execute(context, hal)
    finally:
        item.cleanup(context, hal)
    if not results:
        return normalize_shmoo({})
    return normalize_shmoo(results[0].extra.get("shmoo"))
