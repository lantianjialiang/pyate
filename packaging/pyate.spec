# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one folder, console CLI plus a windowed GUI exe.

Both executables share ``_internal`` (the bundled interpreter and PySide6).
The folder does not need a Python install on the target machine.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
ICON = ROOT / "packaging" / "pyate.ico"
ICON_PNG = SRC / "pyate" / "public" / "apple-touch-icon.png"
import sys

sys.path.insert(0, str(SRC))

hidden = collect_submodules("pyate")
hidden += ["yaml", "pluggy", "pydantic", "pydantic_core"]
excludes = ["pytest", "pystdf", "tkinter", "matplotlib", "numpy", "IPython"]


def _analysis(script: str) -> Analysis:
    return Analysis(
        [str(ROOT / "tools" / script)],
        pathex=[str(SRC)],
        binaries=[],
        datas=[(str(ICON_PNG), "pyate/public")],
        hiddenimports=hidden,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
    )


cli = _analysis("pyate_entry.py")
gui = _analysis("pyate_gui_entry.py")
MERGE((cli, "pyate", "pyate"), (gui, "pyate-gui", "pyate-gui"))

pyz_cli = PYZ(cli.pure, cli.zipped_data)
pyz_gui = PYZ(gui.pure, gui.zipped_data)

exe_cli = EXE(
    pyz_cli,
    cli.scripts,
    [],
    exclude_binaries=True,
    name="pyate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ICON),
)
exe_gui = EXE(
    pyz_gui,
    gui.scripts,
    [],
    exclude_binaries=True,
    name="pyate-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
)

coll = COLLECT(
    exe_cli,
    cli.binaries,
    cli.zipfiles,
    cli.datas,
    exe_gui,
    gui.binaries,
    gui.zipfiles,
    gui.datas,
    strip=False,
    upx=False,
    name="pyate",
)
