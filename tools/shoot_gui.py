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
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SIZE = (1440, 880)

#: (file suffix, tab index) in the order the tabs are added by ``MainWindow``
TABS = [
    ("config", 0),
    ("testflow", 1),
    ("limits", 2),
    ("run", 3),
    ("pattern", 4),
    ("stdf", 5),
]


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


def _wait_paused(window, timeout: float = 180.0) -> bool:
    """Pump the event loop until the run stops at a breakpoint."""
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if window.run_panel.vm.paused_at is not None:
            for _ in range(3):
                QCoreApplication.processEvents()
            return True
        time.sleep(0.05)
    return False


def _shoot_summary(window, out: Path, suffix: str) -> Path:
    """Render Run -> Summary with the first numeric test selected."""
    from PySide6.QtCore import QCoreApplication

    panel = window.run_panel
    panel.view_tabs.setCurrentWidget(panel.summary_page)
    QCoreApplication.processEvents()
    table = panel.summary_table
    n_column = None
    for column in range(table.columnCount()):
        header = table.horizontalHeaderItem(column)
        if header is not None and header.text() == "n":
            n_column = column
            break
    if n_column is None:
        return _shoot(window, out / f"gui_10_summary{suffix}.png")
    for row in range(table.rowCount()):
        item = table.item(row, n_column)
        if item is not None and item.text().strip().isdigit() and int(item.text()) > 0:
            table.selectRow(row)
            break
    return _shoot(window, out / f"gui_10_summary{suffix}.png")


def _shmoo_params(session_path: str) -> dict[str, Any]:
    """Parameters of the ``shmoo`` node of ``session_path`` (empty if none)."""
    from pyate.config.loader import load_session

    config = load_session(session_path)
    stack = list(config.program.flow(config.run.stage))
    while stack:
        node = stack.pop(0)
        if node.item == "shmoo":
            return dict(node.params)
        stack.extend(node.items or [])
    return {}


def _shoot_shmoo(window, session_path: str, out: Path, suffix: str) -> Path:
    """Characterize ``session_path`` and render the Run -> Shmoo page."""
    from pyate.api import Session, run_shmoo

    window.load_session(session_path)
    window.run_panel.clear_results()  # no leftovers from the demo_cp run
    data = run_shmoo(Session(session_path), params=_shmoo_params(session_path))
    window.run_panel.vm.set_shmoo(data)
    window.run_panel.shmoo.set_data(data)
    window.tabs.setCurrentWidget(window.run_panel)
    window.run_panel.view_tabs.setCurrentWidget(window.run_panel.shmoo_page)
    return _shoot(window, out / f"gui_8_shmoo{suffix}.png")


def main(argv: list[str] | None = None) -> int:
    """Render the screenshots and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=str(ROOT / "configs" / "demo_cp" / "session.yaml"))
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "images")
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    parser.add_argument("--run", action="store_true", help="also run one wafer to fill the maps")
    parser.add_argument("--sites", type=int, default=2)
    parser.add_argument("--shmoo", nargs="?", const=str(ROOT / "configs" / "demo_shmoo" / "session.yaml"),
                        metavar="SESSION", help="also render the shmoo page of this session")
    parser.add_argument("--plugins", action="store_true",
                        help="also load examples/plugins and render the Plugins page")
    parser.add_argument("--paused", type=int, metavar="TEST_NUM", default=None,
                        help="arm a breakpoint there and render the paused Run page")
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

    for name, index in TABS:
        window.tabs.setCurrentIndex(index)
        _shoot(window, args.out / f"gui_{index + 1}_{name}{suffix}.png")

    if args.run:
        # artifacts of the demo run stay out of the docs tree
        out_dir = ROOT / "out" / "gui_shots"
        window.tabs.setCurrentWidget(window.run_panel)
        window.run_panel.output_dir = str(out_dir)
        window.run_panel.extra_overrides = {"lot.wafers": ["W01"]}
        if args.paused is not None:
            window.run_panel.breakpoints.add(args.paused)
            window.run_panel._show_breakpoints()
        handle = window.run_smoke(sites=args.sites)
        if handle is None:
            return 1
        if args.paused is not None:
            if not _wait_paused(window):
                print("run never reached the breakpoint", file=sys.stderr)
            window.tabs.setCurrentWidget(window.run_panel)
            _shoot(window, args.out / f"gui_11_paused{suffix}.png")
            window.run_panel.resume_run()
        if not handle.wait(300.0):
            print("run did not finish", file=sys.stderr)
            return 1
        window.tabs.setCurrentWidget(window.run_panel)
        window.run_panel.view_tabs.setCurrentWidget(window.run_panel.wafer_page)
        _shoot(window, args.out / f"gui_7_run_done{suffix}.png")
        _shoot_summary(window, args.out, suffix)
        stdf_files = sorted(out_dir.glob("*.stdf"))
        if stdf_files:
            window.stdf_panel.load_file(str(stdf_files[0]))
            window.tabs.setCurrentWidget(window.stdf_panel)
            _shoot(window, args.out / f"gui_6_stdf{suffix}.png")

    if args.shmoo:
        _shoot_shmoo(window, args.shmoo, args.out, suffix)

    if args.plugins:
        from pyate.api import set_plugin_enabled

        window.plugin_panel.load_path(str(ROOT / "examples" / "plugins"))
        window.plugin_panel.select_plugin("plugin_adc_dut")
        window.tabs.setCurrentWidget(window.plugin_panel)
        _shoot(window, args.out / f"gui_9_plugins{suffix}.png")
        for name in ("failwatch", "plugin_adc_dut", "plugin_json_sink"):
            set_plugin_enabled(name, False)

    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
