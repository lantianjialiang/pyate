"""Plugin package scaffolding (``pyate plugin new``)."""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["create_plugin", "PLUGIN_TEMPLATE", "PYPROJECT_TEMPLATE"]

PLUGIN_TEMPLATE = '''"""{name}: a pyate plugin."""

from __future__ import annotations

from typing import Any

from pyate.plugins.hookspec import API_VERSION, hookimpl

__version__ = "0.1.0"


class Plugin:
    """Entry object registered through the ``pyate.plugins`` entry point."""

    @hookimpl
    def pyate_api_version(self) -> str:
        """Plugin API version this plugin was written against."""
        return API_VERSION

    @hookimpl
    def pyate_register(self, registry: Any) -> None:
        """Register the extension points this plugin provides."""
        registry.note("{name} loaded")
        # from . import items, dut
        # registry.add_testitem("my_item", items.MyItem)
        # registry.add_dut("my_dut", dut.MyDut)
'''

PYPROJECT_TEMPLATE = '''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
description = "pyate plugin"
requires-python = ">=3.10"
dependencies = ["pyate>=0.1"]

[project.entry-points."pyate.plugins"]
{module} = "{module}:Plugin"

[tool.setuptools]
packages = ["{module}"]
'''


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not slug:
        raise ValueError(f"invalid plugin name '{name}'")
    if slug[0].isdigit():
        slug = f"plugin_{slug}"
    return slug


def create_plugin(name: str, *, directory: str | Path | None = None,
                  force: bool = False) -> Path:
    """Create a plugin skeleton and return its directory.

    The generated package registers itself through the ``pyate.plugins`` entry
    point, so ``pip install -e`` is enough to make it visible to pyate.
    """
    module = _slug(name)
    root = Path(directory) if directory is not None else Path.cwd()
    target = root / module
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")
    package = target / module
    package.mkdir(parents=True, exist_ok=True)
    (target / "pyproject.toml").write_text(
        PYPROJECT_TEMPLATE.format(name=name, module=module), encoding="utf-8")
    (package / "__init__.py").write_text(
        PLUGIN_TEMPLATE.format(name=name), encoding="utf-8")
    (target / "README.md").write_text(
        f"# {name}\n\nA pyate plugin.\n\n"
        f"Install with:\n\n```\npip install -e .\n```\n\n"
        f"Then check `pyate plugin list` shows `{module}`.\n", encoding="utf-8")
    return target
