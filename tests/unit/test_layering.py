"""Layering guards: the core never imports Qt, the GUI only imports ``pyate.api``."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import ROOT

SRC = ROOT / "src" / "pyate"
# ``cli`` is excluded on purpose: its ``gui`` command imports PySide6 lazily,
# which is what keeps ``pyate --version`` working without the GUI extra.
CORE_PACKAGES = (
    "api", "batch", "config", "datalog", "dut", "engine",
    "hal", "pattern", "plugins", "scenario", "testitems",
)


def _modules() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_names(path: Path) -> list[tuple[int, str]]:
    """Absolute module names imported by ``path`` (relative ones resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = path.parent.relative_to(SRC.parent)          # e.g. pyate/gui
    package_parts = package.parts
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package_parts)
                for _ in range(node.level - 1):
                    if base:
                        base.pop()
                module = ".".join([*base, node.module]) if node.module else ".".join(base)
            else:
                module = node.module or ""
            found.append((node.lineno, module))
            for alias in node.names:  # ``from x import y`` may import a submodule
                found.append((node.lineno, f"{module}.{alias.name}" if module else alias.name))
    return found


def test_every_module_is_parsable() -> None:
    for path in _modules():
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("package", CORE_PACKAGES)
def test_core_never_imports_the_gui(package: str) -> None:
    offenders: list[str] = []
    for path in (SRC / package).rglob("*.py"):
        for line, module in _imported_names(path):
            if module == "pyate.gui" or module.startswith("pyate.gui."):
                offenders.append(f"{path.relative_to(ROOT)}:{line} -> {module}")
    assert offenders == [], offenders


def test_core_never_imports_pyside() -> None:
    offenders: list[str] = []
    for package in CORE_PACKAGES:
        for path in (SRC / package).rglob("*.py"):
            for line, module in _imported_names(path):
                if module.split(".")[0] == "PySide6":
                    offenders.append(f"{path.relative_to(ROOT)}:{line} -> {module}")
    assert offenders == [], offenders


def test_gui_only_depends_on_the_api_facade() -> None:
    allowed = {"pyate", "pyate.api"}
    offenders: list[str] = []
    for path in (SRC / "gui").rglob("*.py"):
        for line, module in _imported_names(path):
            if not module.startswith("pyate"):
                continue
            if module in allowed or module.startswith("pyate.api."):
                continue
            if module == "pyate.gui" or module.startswith("pyate.gui."):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{line} -> {module}")
    assert offenders == [], offenders


def test_api_does_not_import_gui_or_qt() -> None:
    for path in (SRC / "api").rglob("*.py"):
        for _line, module in _imported_names(path):
            assert not module.startswith("pyate.gui")
            assert module.split(".")[0] != "PySide6"


def test_cli_only_imports_the_gui_lazily() -> None:
    """Every gui import in the CLI must sit inside a function body."""
    tree = ast.parse((SRC / "cli" / "main.py").read_text(encoding="utf-8"))
    module_level: set[int] = set()
    for node in tree.body:
        for child in ast.walk(node):
            module_level.add(id(child))
    offenders: list[str] = []
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.ImportFrom):
            target = node.module or ""
        elif isinstance(node, ast.Import):
            target = ",".join(alias.name for alias in node.names)
        if target and "gui" in target and "pyate" in target:
            if id(node) in module_level:
                offenders.append(f"line {node.lineno}: {target}")
    assert offenders == [], offenders


def test_cli_does_not_import_gui_eagerly() -> None:
    """``pyate --version`` must work on a machine without PySide6."""
    tree = ast.parse((SRC / "cli" / "main.py").read_text(encoding="utf-8"))
    top_level: list[str] = []
    for node in tree.body:                     # module level only
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level.append(node.module or "")
    assert not any(name.startswith("PySide6") for name in top_level)
    assert not any(name.startswith("pyate.gui") or name == "..gui" for name in top_level)
