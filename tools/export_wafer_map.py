"""Render a wafer map PNG from a run directory (no matplotlib needed).

Reads ``<run>/<unit>_binmap.csv`` (or any ``x,y,bin`` CSV) and paints it with the
same widget the GUI uses, so the picture in the docs matches what operators see.

Usage::

    python tools/export_wafer_map.py out/cli_run/W01_binmap.csv \
        --wafer-size 300 --die-size 10 --out out/cli_run/W01_map.png
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def read_binmap(path: Path) -> dict[tuple[int, int], int]:
    """Parse a ``binmap.csv`` written by the CP scenario."""
    dies: dict[tuple[int, int], int] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV")
        for row in reader:
            try:
                x = int(float(row["x"]))
                y = int(float(row["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            raw = row.get("bin") or row.get("soft_bin") or "0"
            if raw in ("", None):
                continue
            dies[(x, y)] = int(float(raw))
    return dies


def main(argv: list[str] | None = None) -> int:
    """Render ``csv`` into ``--out`` and print a one line summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--wafer-size", type=float, default=300.0)
    parser.add_argument("--die-size", type=float, default=10.0)
    parser.add_argument("--size", type=int, default=720, help="image side in pixels")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from pyate.gui.app import ensure_fonts
    from pyate.gui.widgets import WaferMapWidget

    dies = read_binmap(args.csv)
    if not dies:
        raise SystemExit(f"{args.csv}: no dies found")
    target = args.out or args.csv.with_name(args.csv.stem.replace("_binmap", "") + "_map.png")

    app = QApplication.instance() or QApplication([])
    ensure_fonts()  # headless Qt often reports zero font families
    widget = WaferMapWidget()
    widget.resize(args.size, args.size)
    widget.set_data(dies, wafer_size_mm=args.wafer_size, die_size_mm=args.die_size)
    widget.show()
    app.processEvents()
    if not widget.export_png(str(target)):
        raise SystemExit(f"could not write {target}")
    total = sum(1 for bin_num in dies.values() if bin_num != 0)  # bin 0 = not tested
    passed = sum(1 for bin_num in dies.values() if bin_num == 1)
    yield_pct = 100.0 * passed / total if total else 0.0
    print(f"{target}  dies={len(dies)}  tested={total}  pass={passed}  yield={yield_pct:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
