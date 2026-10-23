"""Plugin system: hook specs, loader, registry and scaffolding."""

from __future__ import annotations

from .hookspec import API_VERSION, PROJECT_NAME, PyateSpec, hookimpl, hookspec
from .loader import (
    ENV_PLUGIN_PATH,
    HookBridge,
    HookPolicy,
    PluginManager,
    PluginRegistry,
    get_manager,
    load_plugins,
    reset_manager,
)
from .scaffold import create_plugin

__all__ = [
    "API_VERSION",
    "ENV_PLUGIN_PATH",
    "HookBridge",
    "HookPolicy",
    "PROJECT_NAME",
    "PluginManager",
    "PluginRegistry",
    "PyateSpec",
    "create_plugin",
    "get_manager",
    "hookimpl",
    "hookspec",
    "load_plugins",
    "reset_manager",
]
