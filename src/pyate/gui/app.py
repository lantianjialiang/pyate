"""PySide6 application entry point (``pyate gui``)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["main", "create_app", "create_window", "ensure_fonts", "application_icon"]

_ICON_NAME = "apple-touch-icon.png"

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


def icon_file() -> Path | None:
    """Path to the application icon, in a source tree or a frozen folder."""
    candidates = [
        Path(__file__).resolve().parents[1] / "public" / _ICON_NAME,
        Path(getattr(sys, "_MEIPASS", "")) / "pyate" / "public" / _ICON_NAME,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def application_icon():  # noqa: ANN201 - Qt type is optional
    """The chip icon from ``pyate/public``."""
    from PySide6.QtGui import QIcon

    path = icon_file()
    if path is None:
        return QIcon()
    return QIcon(str(path))


def create_app(argv: list[str] | None = None):  # noqa: ANN201 - Qt type is optional
    """Create (or reuse) the ``QApplication``."""
    from PySide6.QtWidgets import QApplication

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pyate.ate.simulator")
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [])
        app.setApplicationName("pyate")
        app.setApplicationDisplayName("pyate ATE simulator")
    app.setWindowIcon(application_icon())
    ensure_fonts()
    return app


def create_window(session: str | Path | None = None, *, theme: str = "light"):  # noqa: ANN201
    """Create the main window (also used by the headless smoke test)."""
    from .main_window import MainWindow

    return MainWindow(session, theme=theme)


def main(session: str | Path | None = None, *, theme: str = "light") -> int:
    """Run the GUI event loop."""
    if os.environ.get("PYATE_GUI_OFFSCREEN"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_app([])
    window = create_window(session, theme=theme)
    window.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
