"""Windowed entry for ``pyate-gui.exe``.

Double-click opens the GUI. Extra arguments are forwarded, so
``pyate-gui.exe configs\\demo_intro\\session.yaml --theme dark`` still works.
"""

from __future__ import annotations

import multiprocessing
import sys

multiprocessing.freeze_support()

from pyate.cli.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["gui", *sys.argv[1:]]))
