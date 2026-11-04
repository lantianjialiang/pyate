"""Atomic replacement with bounded handling of transient Windows sharing locks."""
import os
import time
from pathlib import Path


def replace_atomic(source: Path, target: Path) -> None:
    """Keep the old file intact until replacement; permanent failures propagate."""
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.01 * 2 ** attempt, .1))
