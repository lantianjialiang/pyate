"""TestItem registry."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from .base import TestItem

__all__ = ["register_testitem", "get_testitem_class", "create_testitem",
           "list_testitems", "is_registered"]

_REGISTRY: dict[str, type[TestItem]] = {}
T = TypeVar("T", bound=type[TestItem])
_LOADED = False


def _ensure_builtins() -> None:
    global _LOADED
    if not _LOADED:
        from . import builtin  # noqa: F401  (registration side effect)

        _LOADED = True


def register_testitem(name: str | None = None) -> Callable[[T], T]:
    """Class decorator registering a test item under ``name``."""

    def decorator(cls: T) -> T:
        key = name or getattr(cls, "name", cls.__name__.lower())
        _REGISTRY[str(key)] = cls
        cls.name = str(key)
        return cls

    return decorator


def get_testitem_class(name: str) -> type[TestItem]:
    """Look up an item class by name."""
    _ensure_builtins()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown test item '{name}' (available: {', '.join(sorted(_REGISTRY))})"
        ) from exc


def is_registered(name: str) -> bool:
    """True when ``name`` is a known item."""
    _ensure_builtins()
    return name in _REGISTRY


def list_testitems() -> list[str]:
    """Every registered item name."""
    _ensure_builtins()
    return sorted(_REGISTRY)


def create_testitem(name: str, test_num: int, params: dict[str, Any] | None = None,
                    *, defn_name: str | None = None) -> TestItem:
    """Instantiate a registered item with validated parameters."""
    cls = get_testitem_class(name)
    return cls(test_num, params, defn_name=defn_name or name)
