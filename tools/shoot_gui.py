"""Render screenshots of the pyate GUI (headless, no display server needed).

Usage:
    QT_QPA_PLATFORM=offscreen python tools/shoot_gui.py --out docs/images

The screenshots are produced by the real ``MainWindow``: the same widgets,
the same style sheet and the same view models used by ``pyate gui``.  Nothing
is mocked, so a screenshot also acts as a visual regression check.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SIZE = (1440, 880)


def _shoot(window, path: Path) -> Path:
    """Grab ``window`` into ``path`` (PNG)."""
    from PySide6.QtCore import QCoreApplication

    for _ in range(3):
        QCoreApplication.processEvents()
    pixmap = window.grab()
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(path))
    print(f"{path}  {pixmap.width()}x{pixmap.height()}")
    return path


def main(argv: list[str] | None = None) -> int:
    """Render the screenshots and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=str(ROOT / "configs" / "demo_cp" / "session.yaml"))
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "images")
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    parser.add_argument("--run", action="store_true", help="also run one wafer to fill the maps")
    parser.add_argument("--sites", type=int, default=2)
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyate.gui.app import create_app, ensure_fonts
    from pyate.gui.main_window import MainWindow

    app = create_app([])
    ensure_fonts()
    window = MainWindow(args.session, theme=args.theme)
    window.resize(*SIZE)
    window.show()
    suffix = "" if args.theme == "dark" else "_light"

    for index, name in enumerate(["config", "testflow", "limits", "run", "batch", "pattern"]):
        window.tabs.setCurrentIndex(index)
        _shoot(window, args.out / f"gui_{index + 1}_{name}{suffix}.png")

    if args.run:
        # artifacts of the demo run stay out of the docs tree
        out_dir = ROOT / "out" / "gui_shots"
        window.tabs.setCurrentWidget(window.run_panel)
        window.run_panel.output_dir = str(out_dir)
        window.run_panel.extra_overrides = {"lot.wafers": ["W01"]}
        handle = window.run_smoke(sites=args.sites)
        if handle is None:
            return 1
        if not handle.wait(300.0):
            print("run did not finish", file=sys.stderr)
            return 1
        window.tabs.setCurrentWidget(window.run_panel)
        _shoot(window, args.out / f"gui_7_run_done{suffix}.png")

    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
