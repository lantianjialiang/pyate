"""Console entry for the frozen ``pyate.exe``.

PyInstaller runs this file as ``__main__``. It uses absolute imports so the
package still resolves after the sources are packed.
"""

from __future__ import annotations

import multiprocessing

multiprocessing.freeze_support()

from pyate.cli.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
