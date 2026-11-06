"""Plugin management used by the GUI (and reusable from scripts).

Everything here is Qt free: the GUI plugin page only calls these functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..plugins.discovery import default_plugin_dirs, scan_candidates
from ..plugins.loader import PluginManager, get_manager, reset_manager

__all__ = [
    "load_plugin",
    "plugin_paths",
    "plugin_report",
    "reload_plugins",
    "scan_plugins",
    "set_plugin_enabled",
]


def _manager(manager: PluginManager | None = None) -> PluginManager:
    return manager if manager is not None else get_manager()


def plugin_paths() -> list[str]:
    """Directories searched for plugins (env var first, then bundled examples)."""
    return [str(path) for path in default_plugin_dirs()]


def plugin_report(manager: PluginManager | None = None) -> dict[str, Any]:
    """Everything the plugin page needs: loaded plugins, registry and messages."""
    info = _manager(manager).info()
    return {
        "api_version": info["api_version"],
        "policy": info["policy"],
        "loaded": info["loaded"],
        "plugins": info["plugins"],
        "registry": info["registry"],
        "messages": info["messages"],
        "warnings": info["warnings"],
        "errors": info["errors"],
        "rejected": info["rejected"],
        "paths": plugin_paths(),
        "total_contributions": sum(len(entry["contributions"]) for entry in info["plugins"]),
    }


def scan_plugins(paths: list[str | Path] | None = None,
                 manager: PluginManager | None = None) -> list[dict[str, Any]]:
    """Candidates found on disk, each flagged with its state.

    ``state`` is ``"loaded"`` (enabled), ``"disabled"`` (known but switched off)
    or ``"not loaded"``; ``loaded`` stays true for both of the first two so the
    two tables of the plugin page cannot disagree about a plugin the manager
    knows.  Candidates are matched by name first, then by source path, because
    a project may be loaded from its root while the candidate is its package
    directory.
    """
    known = {entry["name"]: entry for entry in _manager(manager).describe()}
    entries: list[dict[str, Any]] = []
    for candidate in scan_candidates(list(paths) if paths else None):
        entry = known.get(candidate.name) or _match_by_source(known, candidate.path)
        state = "not loaded"
        if entry is not None:
            state = "loaded" if entry.get("enabled") else "disabled"
        data = candidate.to_dict()
        data["loaded"] = state != "not loaded"
        data["state"] = state
        data["enabled"] = bool(entry and entry.get("enabled"))
        entries.append(data)
    return entries


def _match_by_source(known: dict[str, dict[str, Any]],
                     path: Path) -> dict[str, Any] | None:
    """The known plugin whose source is the same file/directory as ``path``."""
    target = _resolved(path)
    if target is None:
        return None
    for entry in known.values():
        source = _resolved(entry.get("source") or "")
        if source is None:
            continue
        if target == source or source in target.parents:
            # same directory, or the candidate is the project root of ``source``
            return entry
    return None


def _resolved(path: str | Path) -> Path | None:
    try:
        return Path(path).resolve()
    except OSError:  # pragma: no cover - defensive
        return None


def load_plugin(path: str | Path, manager: PluginManager | None = None) -> dict[str, Any]:
    """Load one file/package, register its hooks and return a fresh report."""
    target = _manager(manager)
    location = Path(path)
    ok = target.load_dir(location) if location.is_dir() else target.load_file(location)
    if ok:
        target.finalize()
    report = plugin_report(target)
    report["loaded_now"] = bool(ok)
    report["requested"] = str(location)
    return report


def set_plugin_enabled(name: str, enabled: bool,
                       manager: PluginManager | None = None) -> dict[str, Any]:
    """Enable or disable one plugin (contributions follow the plugin)."""
    target = _manager(manager)
    changed = target.enable(name) if enabled else target.unload(name)
    report = plugin_report(target)
    report["changed"] = bool(changed)
    report["requested"] = name
    return report


def reload_plugins(paths: list[str | Path] | None = None) -> dict[str, Any]:
    """Drop the process wide manager and load everything again.

    Plugins loaded by hand (outside ``PYATE_PLUGIN_PATH`` and the bundled
    ``examples/plugins``) are remembered and loaded again, so a reload does not
    silently forget them.
    """
    manager = get_manager()
    defaults = [Path(item) for item in plugin_paths()]
    extra: list[Path] = []
    for source in manager.sources.values():
        location = Path(source)
        if not (location.is_dir() or location.suffix == ".py"):
            continue  # entry points have no path
        if any(_inside(location, root) for root in defaults):
            continue
        if location not in extra:
            extra.append(location)
    reset_manager()
    fresh = get_manager()
    for location in extra:
        if location.is_dir():
            fresh.load_dir(location)
        else:
            fresh.load_file(location)
    for path in paths or ():
        fresh.load_dir(path) if Path(path).is_dir() else fresh.load_file(path)
    fresh.finalize()
    return plugin_report(fresh)


def _inside(location: Path, root: Path) -> bool:
    try:
        location.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True
