"""Plugin discovery, loading and isolated hook dispatch."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Sequence

import pluggy

from .hookspec import API_VERSION, PROJECT_NAME, PyateSpec

__all__ = [
    "HookPolicy",
    "PluginRegistry",
    "PluginManager",
    "HookBridge",
    "get_manager",
    "load_plugins",
    "reset_manager",
]

_LOG = logging.getLogger("pyate.plugins")

#: Directory scanned for plugins in addition to the installed entry points.
ENV_PLUGIN_PATH = "PYATE_PLUGIN_PATH"
_MODULE_PREFIX = "pyate_plugin_"


class HookPolicy(str, Enum):
    """What to do when a plugin hook raises."""

    IGNORE = "ignore"
    WARN = "warn"
    ABORT = "abort"


@dataclass
class PluginRegistry:
    """Collects everything a plugin wants to add to the kernel."""

    dut_models: dict[str, type] = field(default_factory=dict)
    testitems: dict[str, type] = field(default_factory=dict)
    sinks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    instruments: dict[str, Any] = field(default_factory=dict)
    scenarios: dict[str, Any] = field(default_factory=dict)
    config_sections: dict[str, Any] = field(default_factory=dict)
    binning_strategies: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- register
    def register_dut(self, name: str | None = None) -> Callable[[type], type]:
        """Decorator registering a DUT model class."""
        from ..dut.registry import register_dut

        return register_dut(name)

    def register_testitem(self, name: str | None = None) -> Callable[[type], type]:
        """Decorator registering a test item class."""
        from ..testitems.registry import register_testitem

        return register_testitem(name)

    def add_dut(self, name: str, cls: type) -> None:
        """Register a DUT model under ``name``."""
        from ..dut.registry import register_dut

        register_dut(name)(cls)
        self.dut_models[name] = cls

    def add_testitem(self, name: str, cls: type) -> None:
        """Register a test item under ``name``."""
        from ..testitems.registry import register_testitem

        register_testitem(name)(cls)
        self.testitems[name] = cls

    def add_sink(self, name: str, factory: Callable[..., Any]) -> None:
        """Register a datalog sink factory (``factory(**kwargs)``)."""
        self.sinks[name] = factory

    def add_instrument(self, name: str, impl: Any) -> None:
        """Register a new instrument category."""
        self.instruments[name] = impl

    def add_scenario(self, name: str, impl: Any) -> None:
        """Register a scenario implementation."""
        self.scenarios[name] = impl

    def add_config_section(self, name: str, model: Any) -> None:
        """Register an extra configuration section (pydantic model)."""
        self.config_sections[name] = model

    def add_binning_strategy(self, name: str, impl: Any) -> None:
        """Register a custom binning strategy."""
        self.binning_strategies[name] = impl

    def note(self, message: str) -> None:
        """Record an informational message shown by ``pyate plugin list``."""
        self.messages.append(message)

    def summary(self) -> dict[str, list[str]]:
        """Names registered through the registry."""
        return {
            "dut_models": sorted(self.dut_models),
            "testitems": sorted(self.testitems),
            "sinks": sorted(self.sinks),
            "instruments": sorted(self.instruments),
            "scenarios": sorted(self.scenarios),
            "config_sections": sorted(self.config_sections),
            "binning_strategies": sorted(self.binning_strategies),
        }


class HookBridge:
    """Object handed to the engine as ``hooks=``; dispatches to pluggy."""

    def __init__(self, manager: "PluginManager") -> None:
        self._manager = manager

    def before_lot(self, **kwargs: Any) -> None:
        self._manager.call("before_lot", **kwargs)

    def before_unit(self, **kwargs: Any) -> None:
        self._manager.call("before_unit", **kwargs)

    def after_unit(self, **kwargs: Any) -> None:
        self._manager.call("after_unit", **kwargs)

    def after_lot(self, **kwargs: Any) -> None:
        self._manager.call("after_lot", **kwargs)

    def before_part(self, **kwargs: Any) -> None:
        self._manager.call("before_part", **kwargs)

    def after_part(self, **kwargs: Any) -> None:
        self._manager.call("after_part", **kwargs)

    def before_item(self, **kwargs: Any) -> None:
        self._manager.call("before_item", **kwargs)

    def after_item(self, **kwargs: Any) -> None:
        self._manager.call("after_item", **kwargs)

    def on_fail(self, **kwargs: Any) -> None:
        self._manager.call("on_fail", **kwargs)


class PluginManager:
    """Wraps :class:`pluggy.PluginManager` with pyate specific policies."""

    def __init__(self, api_version: str = API_VERSION,
                 policy: HookPolicy = HookPolicy.WARN) -> None:
        self.api_version = api_version
        self.policy = policy
        self.pm = pluggy.PluginManager(PROJECT_NAME)
        self.pm.add_hookspecs(PyateSpec)
        self.registry = PluginRegistry()
        self.loaded: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._names: dict[int, str] = {}
        self.rejected: list[str] = []
        self.bridge = HookBridge(self)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ load
    def load_entrypoints(self) -> list[str]:
        """Load plugins declared through the ``pyate.plugins`` entry point group."""
        names: list[str] = []
        try:
            self.pm.load_setuptools_entrypoints(PROJECT_NAME)
        except Exception as exc:  # noqa: BLE001 - never break on a broken dist
            self.errors.append(f"entry point scan failed: {exc}")
            return names
        for plugin in self.pm.get_plugins():
            names.append(self._name_of(plugin))
        self.loaded.extend(n for n in names if n not in self.loaded)
        return names

    def load_module(self, module: ModuleType) -> bool:
        """Register an imported module as a plugin.

        Two layouts are supported: hooks declared at module level, or hooks
        declared on a ``Plugin`` class (instantiated here). The class form keeps
        plugin state isolated per registration.
        """
        raw_name = getattr(module, "__name__", str(module))
        name = raw_name[len(_MODULE_PREFIX):] if raw_name.startswith(_MODULE_PREFIX) else raw_name
        target: Any = module
        candidate = getattr(module, "Plugin", None)
        if isinstance(candidate, type):
            try:
                target = candidate()
            except Exception as exc:  # noqa: BLE001 - isolate broken constructors
                self.errors.append(f"{name}: Plugin() failed: {type(exc).__name__}: {exc}")
                return False
        try:
            self.pm.register(target, name=name)
        except ValueError:
            return False  # already registered
        except Exception as exc:  # noqa: BLE001 - pluggy rejects malformed plugins
            self.errors.append(f"{name}: register failed: {type(exc).__name__}: {exc}")
            return False
        self._warn_missing_markers(name, target)
        self._names[id(target)] = name
        if name not in self.loaded:
            self.loaded.append(name)
        return True

    def _warn_missing_markers(self, name: str, target: Any) -> None:
        """Flag hook-shaped methods that lack ``@hookimpl`` (pluggy ignores them)."""
        marker = f"{PROJECT_NAME}_impl"
        for hook in dir(PyateSpec):
            if hook.startswith("_"):
                continue
            method = getattr(target, hook, None)
            if method is None or not callable(method):
                continue
            if getattr(method, marker, None) is None:
                self.warnings.append(
                    f"{name}: '{hook}' is missing the @hookimpl decorator and will not run"
                )

    def load_file(self, path: str | Path) -> bool:
        """Import a ``.py`` file or a package directory and register it."""
        file_path = Path(path)
        try:
            if file_path.is_dir():
                init = file_path / "__init__.py"
                if not init.is_file():
                    return False
                module = self._import_from(init, file_path.name)
            elif file_path.suffix == ".py":
                module = self._import_from(file_path, file_path.stem)
            else:
                return False
        except Exception as exc:  # noqa: BLE001 - isolate import failures
            self.errors.append(f"{file_path}: {type(exc).__name__}: {exc}")
            return False
        if module is None:
            return False
        return self.load_module(module)

    @staticmethod
    def _import_from(file_path: Path, name: str) -> ModuleType:
        module_name = f"{_MODULE_PREFIX}{name}"
        if module_name in sys.modules:
            return sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ImportError(f"cannot load {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def load_dir(self, directory: str | Path) -> list[str]:
        """Load every ``*.py`` file and package inside ``directory``."""
        root = Path(directory)
        if not root.is_dir():
            return []
        found: list[str] = []
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.is_file() and entry.suffix == ".py":
                if self.load_file(entry):
                    found.append(entry.stem)
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                if self.load_file(entry):
                    found.append(entry.name)
        return found

    def load_all(self, directories: Sequence[str | Path] | None = None) -> "PluginManager":
        """Entry points first, then explicit directories, then the default ones."""
        with self._lock:
            self.load_entrypoints()
            for directory in directories or ():
                self.load_dir(directory)
            env = os.environ.get(ENV_PLUGIN_PATH, "")
            for chunk in env.split(os.pathsep):
                if chunk.strip():
                    self.load_dir(chunk)
            self._check_versions()
            self._register()
        return self

    def _check_versions(self) -> None:
        for plugin in list(self.pm.get_plugins()):
            name = self._name_of(plugin)
            version = None
            try:
                hooks = self.pm.hook.pyate_api_version
                results = [v for v in hooks() if v]
                version = results[0] if results else None
            except Exception as exc:  # noqa: BLE001 - a broken hook must not abort
                self.errors.append(f"{name}: api_version hook failed: {exc}")
                continue
            if version is not None and not _compatible(str(version), self.api_version):
                self.rejected.append(f"{name} (api {version} != {self.api_version})")
                self.pm.unregister(plugin)
                if name in self.loaded:
                    self.loaded.remove(name)

    def _register(self) -> None:
        try:
            self.pm.hook.pyate_register(registry=self.registry)
        except Exception as exc:  # noqa: BLE001 - registration failures are reported
            self.errors.append(f"pyate_register failed: {type(exc).__name__}: {exc}")

    def _name_of(self, plugin: Any) -> str:
        """Display name of a registered plugin (module or ``Plugin`` instance)."""
        known = self._names.get(id(plugin))
        if known is not None:
            return known
        return getattr(plugin, "__name__", type(plugin).__name__)

    # ------------------------------------------------------------------ call
    def call(self, hook: str, **kwargs: Any) -> list[Any]:
        """Invoke a hook on every plugin, isolating failures per policy."""
        dispatcher = getattr(self.pm.hook, hook, None)
        if dispatcher is None:  # pragma: no cover - unknown hook name
            return []
        try:
            return list(dispatcher(**kwargs))
        except Exception as exc:  # noqa: BLE001 - plugin code is untrusted
            message = f"plugin hook '{hook}' failed: {type(exc).__name__}: {exc}"
            if self.policy is HookPolicy.ABORT:
                raise
            if self.policy is HookPolicy.WARN:
                _LOG.warning(message)
                self.errors.append(message)
            return []

    # ------------------------------------------------------------------ info
    def info(self) -> dict[str, Any]:
        """Status of the manager (used by ``pyate plugin list``)."""
        return {
            "api_version": self.api_version,
            "policy": self.policy.value,
            "loaded": list(self.loaded),
            "rejected": list(self.rejected),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "registry": self.registry.summary(),
        }


def _compatible(plugin_version: str, kernel_version: str) -> bool:
    """Compare ``major.minor`` plugin versions (major must match)."""
    def parts(value: str) -> tuple[int, int]:
        try:
            major, _, rest = str(value).partition(".")
            minor = rest.split(".")[0] if rest else "0"
            return int(major), int(minor or 0)
        except ValueError:
            return (0, 0)

    plugin_major, _ = parts(plugin_version)
    kernel_major, _ = parts(kernel_version)
    return plugin_major == kernel_major


_MANAGER: PluginManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> PluginManager:
    """Process wide plugin manager (loaded on first use)."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = PluginManager().load_all()
        return _MANAGER


def load_plugins(
    directories: Iterable[str | Path] | None = None,
    *,
    policy: HookPolicy = HookPolicy.WARN,
    api_version: str = API_VERSION,
    fresh: bool = False,
) -> PluginManager:
    """Load plugins from entry points and ``directories`` and return the manager."""
    global _MANAGER
    manager = PluginManager(api_version=api_version, policy=policy)
    with _MANAGER_LOCK:
        if fresh or _MANAGER is None:
            _MANAGER = manager
    return manager.load_all(list(directories or []))


def reset_manager() -> None:
    """Drop the cached manager (used by tests)."""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = None
