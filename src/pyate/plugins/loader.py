"""Plugin discovery, loading and isolated hook dispatch."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import logging
import os
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

import pluggy

from .hookspec import API_VERSION, PROJECT_NAME, PyateSpec

__all__ = [
    "HookBridge",
    "HookPolicy",
    "PluginManager",
    "PluginRegistry",
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


class PluginAbort(RuntimeError):
    """A plugin explicitly requested termination under the ABORT policy."""


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
    #: ``"<kind>:<name>"`` → plugin that registered it (used to disable a plugin).
    owners: dict[str, str] = field(default_factory=dict)
    #: ``"<kind>:<name>"`` → value before the first plugin replaced it.
    previous: dict[str, Any] = field(default_factory=dict)
    #: ``plugin name`` → messages it noted (removed together with the plugin).
    plugin_messages: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._current = ""

    def _claim(self, key: str, previous: Any, owner: str | None = None) -> None:
        """Remember which plugin owns ``key`` (and what it replaced)."""
        self.previous.setdefault(key, previous)
        owner = owner if owner is not None else self._current
        if owner:
            self.owners[key] = owner

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
        from ..dut.registry import register_dut, registered_dut

        key = f"dut:{name}"
        self._claim(key, registered_dut(name))
        register_dut(name)(cls)
        self.dut_models[name] = cls

    def add_testitem(self, name: str, cls: type) -> None:
        """Register a test item under ``name``."""
        from ..testitems.registry import register_testitem, registered_testitem

        key = f"testitem:{name}"
        self._claim(key, registered_testitem(name))
        register_testitem(name)(cls)
        self.testitems[name] = cls

    def add_sink(self, name: str, factory: Callable[..., Any]) -> None:
        """Register a datalog sink factory (``factory(**kwargs)``)."""
        self._claim(f"sink:{name}", self.sinks.get(name))
        self.sinks[name] = factory

    def add_instrument(self, name: str, impl: Any) -> None:
        """Register a new instrument category."""
        self._claim(f"instrument:{name}", self.instruments.get(name))
        self.instruments[name] = impl

    def add_scenario(self, name: str, impl: Any) -> None:
        """Register a scenario implementation."""
        self._claim(f"scenario:{name}", self.scenarios.get(name))
        self.scenarios[name] = impl

    def add_config_section(self, name: str, model: Any) -> None:
        """Register an extra configuration section (pydantic model)."""
        self._claim(f"config_section:{name}", self.config_sections.get(name))
        self.config_sections[name] = model

    def add_binning_strategy(self, name: str, impl: Any) -> None:
        """Register a custom binning strategy."""
        self._claim(f"binning:{name}", self.binning_strategies.get(name))
        self.binning_strategies[name] = impl

    def note(self, message: str) -> None:
        """Record an informational message shown by ``pyate plugin list``."""
        if message in self.messages:
            return
        self.messages.append(message)
        if self._current:
            self.plugin_messages.setdefault(self._current, []).append(message)

    def contributions(self, plugin: str) -> list[str]:
        """``kind:name`` entries a plugin added to this registry."""
        return sorted(key for key, owner in self.owners.items() if owner == plugin)

    def remove_plugin(self, plugin: str) -> list[str]:
        """Undo everything ``plugin`` registered; returns the removed keys."""
        from ..dut.registry import register_dut, unregister_dut
        from ..testitems.registry import register_testitem, unregister_testitem

        removed: list[str] = []
        buckets: dict[str, dict[str, Any]] = {
            "dut": self.dut_models,
            "testitem": self.testitems,
            "sink": self.sinks,
            "instrument": self.instruments,
            "scenario": self.scenarios,
            "config_section": self.config_sections,
            "binning": self.binning_strategies,
        }
        for key, owner in list(self.owners.items()):
            if owner != plugin:
                continue
            kind, _, name = key.partition(":")
            previous = self.previous.pop(key, None)
            buckets.get(kind, {}).pop(name, None)
            if kind == "dut":
                unregister_dut(name)
                if previous is not None:
                    register_dut(name)(previous)
            elif kind == "testitem":
                unregister_testitem(name)
                if previous is not None:
                    register_testitem(name)(previous)
            elif previous is not None:
                buckets[kind][name] = previous
            del self.owners[key]
            removed.append(key)
        for message in self.plugin_messages.pop(plugin, []):
            if message in self.messages:
                self.messages.remove(message)
        return removed

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

    def __init__(self, manager: PluginManager) -> None:
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
        #: plugin name → where it came from (file, package or "entry point")
        self.sources: dict[str, str] = {}
        #: plugin name → registered object (module or ``Plugin`` instance)
        self._objects: dict[str, Any] = {}
        #: plugins removed with :meth:`unload`, kept so they can be enabled again
        self._inactive: dict[str, Any] = {}
        #: plugin name → first line of its docstring
        self.docs: dict[str, str] = {}

    # ------------------------------------------------------------------ load
    def load_entrypoints(self) -> list[str]:
        """Load plugins declared through the ``pyate.plugins`` entry point group."""
        names: list[str] = []
        try:
            self.pm.load_setuptools_entrypoints(PROJECT_NAME)
        except Exception as exc:
            self.errors.append(f"entry point scan failed: {exc}")
            return names
        for plugin in self.pm.get_plugins():
            name = self._name_of(plugin)
            names.append(name)
            self.sources.setdefault(name, "entry point")
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
            except Exception as exc:
                self.errors.append(f"{name}: Plugin() failed: {type(exc).__name__}: {exc}")
                return False
        try:
            self.pm.register(target, name=name)
        except ValueError:
            return False  # already registered
        except Exception as exc:
            self.errors.append(f"{name}: register failed: {type(exc).__name__}: {exc}")
            return False
        self._warn_missing_markers(name, target)
        self._names[id(target)] = name
        self._objects[name] = target
        self.docs[name] = _first_line(module) or _first_line(target)
        source = getattr(module, "__file__", "")
        if source:
            self.sources.setdefault(name, str(Path(source).parent if Path(source).name == "__init__.py" else source))
        if name not in self.loaded:
            self.loaded.append(name)
        self._claim_imports(name, module)
        return True

    def _claim_imports(self, name: str, module: ModuleType) -> None:
        """Own classes that registered themselves at import time via decorators.

        ``@register_testitem`` / ``@register_dut`` run while the module is
        imported, before ``pyate_register``. Without this they would survive a
        :meth:`unload` and look like a plugin that refuses to be disabled.
        """
        from ..dut.registry import get_dut_class, list_models
        from ..testitems.registry import get_testitem_class, list_testitems

        prefix = getattr(module, "__name__", "")
        if not prefix:
            return
        pairs: list[tuple[str, type[Any]]] = [(f"dut:{item}", get_dut_class(item)) for item in list_models()]
        pairs += [(f"testitem:{item}", get_testitem_class(item)) for item in list_testitems()]
        for key, cls in pairs:
            module_name = getattr(cls, "__module__", "")
            if module_name == prefix or module_name.startswith(f"{prefix}."):
                self.registry._claim(key, None, owner=name)

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
        except Exception as exc:
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
        """Load every module/package inside ``directory``.

        A sub-directory without ``__init__.py`` but with ``pyproject.toml`` is
        treated as a plugin project (``pyproject.toml`` + ``<name>/__init__.py``),
        which is how packages generated by ``pyate plugin new`` are laid out.
        """
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
            elif entry.is_dir() and (entry / "pyproject.toml").is_file():
                for child in sorted(entry.iterdir()):
                    if (child.is_dir() and not child.name.startswith(("_", ".", "test"))
                            and (child / "__init__.py").is_file() and self.load_file(child)):
                        found.append(child.name)
        return found

    def load_all(self, directories: Sequence[str | Path] | None = None) -> PluginManager:
        """Entry points first, then explicit directories, then the default ones."""
        with self._lock:
            self.load_entrypoints()
            for directory in directories or ():
                self.load_dir(directory)
            env = os.environ.get(ENV_PLUGIN_PATH, "")
            for chunk in env.split(os.pathsep):
                if chunk.strip():
                    self.load_dir(chunk)
            self.finalize()
        return self

    def finalize(self) -> PluginManager:
        """Check API versions and run ``pyate_register`` for everything loaded."""
        with self._lock:
            self._check_versions()
            self._register()
        return self

    # --------------------------------------------------------------- manage
    def unload(self, name: str) -> bool:
        """Disable a plugin: unregister its hooks and undo its registrations."""
        with self._lock:
            target = self._objects.pop(name, None)
            if target is None:
                return False
            with contextlib.suppress(ValueError):
                self.pm.unregister(target)
            self._inactive[name] = target
            if name in self.loaded:
                self.loaded.remove(name)
            self.registry.remove_plugin(name)
            return True

    def enable(self, name: str) -> bool:
        """Re-enable a plugin removed with :meth:`unload`."""
        with self._lock:
            target = self._inactive.pop(name, None)
            if target is None:
                return name in self._objects
            try:
                self.pm.register(target, name=name)
            except Exception as exc:
                self.errors.append(f"{name}: register failed: {type(exc).__name__}: {exc}")
                self._inactive[name] = target
                return False
            self._names[id(target)] = name
            self._objects[name] = target
            if name not in self.loaded:
                self.loaded.append(name)
            self._register_one(target)
            return True

    def is_enabled(self, name: str) -> bool:
        """True when ``name`` is currently registered with pluggy."""
        return name in self._objects

    def describe(self) -> list[dict[str, Any]]:
        """One entry per known plugin (enabled or not), newest fields first."""
        entries: list[dict[str, Any]] = []
        for name in list(self._objects) + [n for n in self._inactive if n not in self._objects]:
            entries.append({
                "name": name,
                "enabled": name in self._objects,
                "source": self.sources.get(name, ""),
                "doc": self.docs.get(name, ""),
                "api_version": self._plugin_version(name),
                "contributions": self.registry.contributions(name),
                "messages": list(self.registry.plugin_messages.get(name, [])),
            })
        return entries

    def _plugin_version(self, name: str) -> str:
        """API version reported by one plugin (empty when it does not say)."""
        target = self._objects.get(name) or self._inactive.get(name)
        method = getattr(target, "pyate_api_version", None)
        if method is None:
            return ""
        try:
            return str(method() or "")
        except Exception:  # pragma: no cover - defensive
            return ""

    def _check_versions(self) -> None:
        for plugin in list(self.pm.get_plugins()):
            name = self._name_of(plugin)
            others = [p for p in self.pm.get_plugins() if p is not plugin]
            try:
                caller = self.pm.subset_hook_caller("pyate_api_version", remove_plugins=others)
                results = [v for v in caller() if v]
                version = results[0] if results else None
            except Exception as exc:
                self.errors.append(f"{name}: api_version hook failed: {exc}")
                continue
            if version is not None and not _compatible(str(version), self.api_version):
                self.rejected.append(f"{name} (api {version} != {self.api_version})")
                self.pm.unregister(plugin)
                self._objects.pop(name, None)
                self._inactive.pop(name, None)
                if name in self.loaded:
                    self.loaded.remove(name)

    def _register(self) -> None:
        for plugin in list(self.pm.get_plugins()):
            self._register_one(plugin)

    def _register_one(self, plugin: Any) -> None:
        """Run ``pyate_register`` for one plugin so its contributions are attributable."""
        name = self._name_of(plugin)
        others = [p for p in self.pm.get_plugins() if p is not plugin]
        self.registry._current = name
        try:
            caller = self.pm.subset_hook_caller("pyate_register", remove_plugins=others)
            caller(registry=self.registry)
        except Exception as exc:
            self.errors.append(f"pyate_register failed: {type(exc).__name__}: {exc}")
        finally:
            self.registry._current = ""

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
        output = []
        plugins = list(self.pm.get_plugins())
        for plugin in sorted(plugins, key=self._name_of):
            caller = self.pm.subset_hook_caller(hook, remove_plugins=[p for p in plugins if p is not plugin])
            try:
                output.extend(caller(**kwargs))
            except Exception as exc:
                message = f"plugin hook '{hook}' failed: {type(exc).__name__}: {exc}"
                if self.policy is HookPolicy.ABORT:
                    raise PluginAbort(message) from exc
                if self.policy is HookPolicy.WARN:
                    _LOG.warning(message)
                    self.errors.append(message)
        return output

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
            "messages": list(self.registry.messages),
            "plugins": self.describe(),
            "sources": dict(self.sources),
        }


def _first_line(target: Any) -> str:
    """First non-empty line of a plugin's docstring (module or ``Plugin`` class)."""
    import inspect

    doc = inspect.getdoc(target) or ""
    if not doc and not inspect.ismodule(target):
        doc = inspect.getdoc(type(target)) or ""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()
    return ""


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
