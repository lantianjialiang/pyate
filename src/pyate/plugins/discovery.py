"""Find plugin files and describe them *without* importing them.

The GUI lists what is available under the search paths, so this module parses
each candidate with :mod:`ast` only. Nothing here executes third party code —
loading still goes through :class:`~pyate.plugins.loader.PluginManager`.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .hookspec import PyateSpec
from .loader import ENV_PLUGIN_PATH

__all__ = [
    "PluginCandidate",
    "default_plugin_dirs",
    "scan_candidates",
]

_HOOKS = frozenset(name for name in dir(PyateSpec) if not name.startswith("_"))


@dataclass(frozen=True)
class PluginCandidate:
    """A plugin that exists on disk but may not be loaded yet."""

    name: str
    path: Path
    kind: str  #: ``"file"`` or ``"package"``
    doc: str = ""
    version: str = ""
    hooks: tuple[str, ...] = ()
    contributions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """Plain dict for the api layer / GUI."""
        return {
            "name": self.name,
            "path": str(self.path),
            "kind": self.kind,
            "doc": self.doc,
            "version": self.version,
            "hooks": list(self.hooks),
        }


def _attr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _hook_names(tree: ast.AST) -> tuple[str, ...]:
    """Methods decorated with ``@hookimpl`` that match a real hook name."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _attr_name(target).endswith("hookimpl") and node.name in _HOOKS:
                found.add(node.name)
                break
    return tuple(sorted(found))


def _version(tree: ast.Module) -> str:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
    return ""


def _read(path: Path) -> PluginCandidate | None:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    return PluginCandidate(
        name=path.parent.name if path.name == "__init__.py" else path.stem,
        path=path if path.name != "__init__.py" else path.parent,
        kind="package" if path.name == "__init__.py" else "file",
        doc=doc[0].strip() if doc else "",
        version=_version(tree),
        hooks=_hook_names(tree),
    )


def default_plugin_dirs() -> list[Path]:
    """Where to look for plugins: ``PYATE_PLUGIN_PATH`` plus the bundled examples."""
    roots: list[Path] = []
    env = os.environ.get(ENV_PLUGIN_PATH, "")
    roots.extend(Path(chunk) for chunk in env.split(os.pathsep) if chunk.strip())
    bases = [Path.cwd()]
    if getattr(sys, "frozen", False):  # PyInstaller: the exe directory ships examples/
        bases.insert(0, Path(sys.executable).resolve().parent)
    roots.extend(base / "examples" / "plugins" for base in bases)
    seen: set[Path] = set()
    result: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:  # pragma: no cover - defensive
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _candidate_inits(entry: Path) -> list[Path]:
    """``__init__.py`` files a directory entry can be loaded from.

    Accepts a single module (``foo.py``), a package (``foo/__init__.py``) and a
    project directory in ``src``-less layout (``foo/pyproject.toml`` +
    ``foo/foo/__init__.py``), which is how the bundled examples are laid out.
    """
    if entry.is_file() and entry.suffix == ".py":
        return [entry]
    if not entry.is_dir():
        return []
    if (entry / "__init__.py").is_file():
        return [entry / "__init__.py"]
    if not (entry / "pyproject.toml").is_file():
        return []
    return [
        child / "__init__.py"
        for child in sorted(entry.iterdir())
        if child.is_dir()
        and not child.name.startswith(("_", ".", "test"))
        and (child / "__init__.py").is_file()
    ]


def scan_candidates(directories: list[str | Path] | None = None) -> list[PluginCandidate]:
    """Every loadable ``*.py`` / package found in ``directories`` (deduplicated)."""
    found: list[PluginCandidate] = []
    seen: set[Path] = set()
    for directory in directories or default_plugin_dirs():
        root = Path(directory)
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            for init in _candidate_inits(entry):
                candidate = _read(init)
                if candidate is None or candidate.path in seen:
                    continue
                seen.add(candidate.path)
                found.append(candidate)
    return found
