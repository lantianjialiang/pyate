"""Build a standalone Windows folder that does not need Python installed.

Run from a development environment that already has PySide6 and PyInstaller:

    python tools/build_windows.py

The result is ``dist/pyate/``. Copy that whole folder to the target machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "pyate.spec"
DIST = ROOT / "dist" / "pyate"
ICON_PNG = ROOT / "src" / "pyate" / "public" / "apple-touch-icon.png"
ICON_ICO = ROOT / "packaging" / "pyate.ico"


def _write_icon() -> None:
    """Turn the public PNG into a multi-size Windows icon."""
    from PIL import Image

    image = Image.open(ICON_PNG).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    image.save(ICON_ICO, format="ICO", sizes=sizes)


def main() -> int:
    if sys.platform != "win32":
        print("this packager builds a Windows folder; run it on Windows", file=sys.stderr)
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed.\n"
              "Install the packager extra:  pip install -e \".[package]\"", file=sys.stderr)
        return 1
    try:
        _write_icon()
    except ImportError:
        print("Pillow is required to build the Windows icon.\n"
              "Install it with:  pip install pillow", file=sys.stderr)
        return 1

    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (";" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        str(SPEC),
    ]
    completed = subprocess.run(command, cwd=ROOT, env=env)
    if completed.returncode != 0:
        return int(completed.returncode)
    if not (DIST / "pyate.exe").is_file():
        print(f"build finished but {DIST / 'pyate.exe'} is missing", file=sys.stderr)
        return 1

    configs = DIST / "configs"
    if configs.exists():
        shutil.rmtree(configs)
    shutil.copytree(ROOT / "configs", configs)
    for name in ("使用说明.txt", "启动界面.bat"):
        shutil.copy2(ROOT / "packaging" / name, DIST / name)
    print(f"standalone folder: {DIST}")
    print("copy this whole folder; the target machine does not need Python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
