"""PySide6 GUI (optional dependency).

Only :mod:`pyate.api` and :mod:`pyate.engine.events` are used here; the GUI never
imports the HAL, the flow executor or any other core internals directly.
Importing this package requires PySide6 (``pip install 'pyate[gui]'``); the
``viewmodels`` subpackage has no Qt dependency and can be used headless.
"""

from __future__ import annotations

__all__ = ["create_app", "create_window", "main"]


def __getattr__(name: str):  # noqa: ANN201 - lazy Qt import
    """Import the Qt layer lazily so ``pyate.gui.viewmodels`` stays Qt free."""
    if name in ("create_app", "create_window", "main"):
        from . import app

        return getattr(app, name)
    raise AttributeError(name)
