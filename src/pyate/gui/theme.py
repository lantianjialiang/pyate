"""Dark / light Qt style sheets for the pyate GUI."""

from __future__ import annotations

__all__ = ["DARK", "LIGHT", "stylesheet", "bins_colors", "canvas_colors", "set_theme"]

#: Bin colours (pass = green, fail = warm tones).  Chinese convention for *test*
#: results is used here: green means good die, red/orange means failing die.
BIN_COLORS: dict[int, str] = {
    1: "#2e9e5b",
    2: "#c0392b",
    3: "#e67e22",
    4: "#d35400",
    5: "#8e44ad",
    6: "#2980b9",
    7: "#16a085",
    8: "#7f8c8d",
    9: "#34495e",
}


def bins_colors() -> dict[int, str]:
    """Colour per soft bin (unknown bins fall back to grey)."""
    return dict(BIN_COLORS)


_COMMON = """
QWidget { font-size: 12px; }
QTabWidget::pane { border: 1px solid %(grid)s; top: -1px; }
QTabBar::tab { padding: 7px 18px; margin-right: 2px; min-width: 64px;
    border-top-left-radius: 5px; border-top-right-radius: 5px; }
QHeaderView::section { padding: 5px 6px; }
QTableWidget { gridline-color: %(grid)s; }
QTableWidget::item { padding: 2px 4px; }
QPushButton { padding: 5px 14px; border-radius: 4px; }
QPushButton:hover { background: %(accent_hover)s; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { padding: 3px 6px; border-radius: 3px; }
QGroupBox { border: 1px solid %(grid)s; border-radius: 5px; margin-top: 10px;
    padding: 8px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QSplitter::handle { background: %(grid)s; }
QSplitter::handle:horizontal { width: 3px; }
QSplitter::handle:vertical { height: 3px; }
QScrollBar:horizontal { background: transparent; height: 12px; }
QScrollBar::handle:horizontal { border-radius: 6px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QStatusBar { border-top: 1px solid %(grid)s; }
QToolTip { padding: 4px 6px; border-radius: 3px; }
"""

DARK = """
QWidget { background: #1e2227; color: #e6e6e6; }
QTabWidget::pane { border: 1px solid #333a42; }
QTabBar::tab { background: #262b31; color: #cfd6dd; border: 1px solid #333a42;
    border-bottom: none; }
QTabBar::tab:hover { background: #313841; }
QTabBar::tab:selected { background: #2f6feb; color: #ffffff; border-color: #2f6feb; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background: #14181c; color: #e6e6e6; border: 1px solid #333a42;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2f6feb;
}
QComboBox QAbstractItemView { background: #14181c; color: #e6e6e6;
    selection-background-color: #2f6feb; }
QPushButton { background: #2f6feb; color: #ffffff; border: none; }
QPushButton:disabled { background: #3a4149; color: #8b949e; }
QPushButton#danger { background: #c0392b; }
QTableWidget { background: #161a1f; alternate-background-color: #1b2026;
    gridline-color: #2c333a; selection-background-color: #2f6feb; }
QHeaderView::section { background: #262b31; color: #cfd6dd; border: none;
    border-right: 1px solid #2c333a; }
QTreeWidget, QListWidget { background: #161a1f; alternate-background-color: #1b2026; }
QScrollBar:vertical { background: #161a1f; width: 12px; }
QScrollBar::handle:vertical { background: #3a4149; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:horizontal { background: #3a4149; }
QScrollBar::add-line, QScrollBar::sub-line { background: none; border: none; }
QLabel#title { font-size: 16px; font-weight: bold; }
QLabel#stat { font-size: 14px; font-weight: bold; color: #58a6ff; }
QGroupBox { background: #22272d; }
QGroupBox::title { color: #cfd6dd; }
QMenuBar { background: #1a1e23; }
QMenuBar::item:selected { background: #2f6feb; }
QMenu { background: #1a1e23; border: 1px solid #333a42; }
QMenu::item:selected { background: #2f6feb; }
QStatusBar { background: #1a1e23; color: #cfd6dd; }
QToolTip { background: #262b31; color: #e6e6e6; border: 1px solid #333a42; }
"""

LIGHT = """
QWidget { background: #f6f7f9; color: #1c1f23; }
QTabWidget::pane { border: 1px solid #d7dbe0; }
QTabBar::tab { background: #e9ecef; color: #33393f; border: 1px solid #d7dbe0;
    border-bottom: none; }
QTabBar::tab:hover { background: #dde2e8; }
QTabBar::tab:selected { background: #2f6feb; color: #ffffff; border-color: #2f6feb; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background: #ffffff; color: #1c1f23; border: 1px solid #ccd2d8;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2f6feb;
}
QComboBox QAbstractItemView { background: #ffffff; color: #1c1f23;
    selection-background-color: #dbe8ff; selection-color: #1c1f23; }
QPushButton { background: #2f6feb; color: #ffffff; border: none; }
QPushButton:disabled { background: #c6ccd3; color: #6b737b; }
QPushButton#danger { background: #c0392b; }
QTableWidget { background: #ffffff; alternate-background-color: #f2f4f7;
    gridline-color: #e1e5ea; selection-background-color: #dbe8ff; }
QHeaderView::section { background: #e9ecef; color: #33393f; border: none;
    border-right: 1px solid #e1e5ea; }
QTreeWidget, QListWidget { background: #ffffff; alternate-background-color: #f7f9fb; }
QScrollBar:vertical { background: #f6f7f9; width: 12px; }
QScrollBar::handle:vertical { background: #c2c9d1; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:horizontal { background: #c2c9d1; }
QScrollBar::add-line, QScrollBar::sub-line { background: none; border: none; }
QLabel#title { font-size: 16px; font-weight: bold; }
QLabel#stat { font-size: 14px; font-weight: bold; color: #1f6feb; }
QGroupBox { background: #ffffff; }
QGroupBox::title { color: #33393f; }
QMenuBar { background: #eef1f4; }
QMenuBar::item:selected { background: #dbe8ff; color: #1c1f23; }
QMenu { background: #ffffff; border: 1px solid #d7dbe0; }
QMenu::item:selected { background: #dbe8ff; color: #1c1f23; }
QStatusBar { background: #eef1f4; color: #33393f; }
QToolTip { background: #ffffff; color: #1c1f23; border: 1px solid #ccd2d8; }
"""


def stylesheet(theme: str = "dark") -> str:
    """Return the style sheet of ``theme`` (``dark`` or ``light``)."""
    light = str(theme).lower() == "light"
    base = DARK if not light else LIGHT
    return _COMMON % {
        "grid": "#e1e5ea" if light else "#2c333a",
        "accent_hover": "#4b84f0" if not light else "#4b84f0",
    } + base


#: Colours used by the hand painted widgets (wafer map, bin bar, shmoo).
_CANVAS: dict[str, dict[str, str]] = {
    "dark": {"bg": "#12151a", "text": "#cfd6dd", "muted": "#7f8c8d", "outline": "#4a5360"},
    "light": {"bg": "#ffffff", "text": "#33393f", "muted": "#7b848d", "outline": "#b9c0c8"},
}

_canvas_theme = "dark"


def set_theme(theme: str) -> str:
    """Remember the active theme for the painted widgets; returns its name."""
    global _canvas_theme
    _canvas_theme = "light" if str(theme).lower() == "light" else "dark"
    return _canvas_theme


def canvas_colors() -> dict[str, str]:
    """Background / text / outline colours matching the active theme."""
    return dict(_CANVAS[_canvas_theme])
