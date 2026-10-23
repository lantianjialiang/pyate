"""PySide6 application entry point (``pyate gui``)."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["main", "create_app", "create_window", "ensure_fonts"]

#: Fonts tried when the platform reports no font families at all (headless Qt).
_FALLBACK_FONTS = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def ensure_fonts() -> list[str]:
    """Register a fallback font if Qt sees no fonts (offscreen/CI runs).

    Returns the family names that were registered (empty when the platform
    already provides fonts, which is the normal desktop case).
    """
    from PySide6.QtGui import QFontDatabase

    if QFontDatabase.families():
        return []
    added: list[str] = []
    for candidate in _FALLBACK_FONTS:
        if not Path(candidate).exists():
            continue
        font_id = QFontDatabase.addApplicationFont(candidate)
        if font_id != -1:
            added.extend(QFontDatabase.applicationFontFamilies(font_id))
        if added:
            break
    return added


def create_app(argv: list[str] | None = None):  # noqa: ANN201 - Qt type is optional
    """Create (or reuse) the ``QApplication``."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [])
        app.setApplicationName("pyate")
        app.setApplicationDisplayName("pyate ATE simulator")
    ensure_fonts()
    return app


def create_window(session: str | Path | None = None, *, theme: str = "dark"):  # noqa: ANN201
    """Create the main window (also used by the headless smoke test)."""
    from .main_window import MainWindow

    return MainWindow(session, theme=theme)


def main(session: str | Path | None = None, *, theme: str = "dark") -> int:
    """Run the GUI event loop."""
    if os.environ.get("PYATE_GUI_OFFSCREEN"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_app([])
    window = create_window(session, theme=theme)
    window.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
