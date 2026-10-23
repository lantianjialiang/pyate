"""PyATE - an offline ATE (Automatic Test Equipment) simulator.

The public API is intentionally small: everything else is importable from
its sub-package (``pyate.config``, ``pyate.engine`` ...).  No module in the
core may import :mod:`pyate.gui`.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
