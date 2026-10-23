"""Shared pytest fixtures for the pyate test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CONFIGS = ROOT / "configs"
CP_SESSION = CONFIGS / "demo_cp" / "session.yaml"
FT_SESSION = CONFIGS / "demo_ft" / "session.yaml"


def small_cp_overrides(**extra: Any) -> dict[str, Any]:
    """Overrides that keep a CP run at a couple of seconds."""
    base: dict[str, Any] = {
        "run.sites": 2,
        "run.seed": 42,
        "lot.wafers": ["W01"],
        "wafer.diameter_mm": 150,
    }
    base.update(extra)
    return base


def small_ft_overrides(**extra: Any) -> dict[str, Any]:
    """Overrides that keep an FT run at a couple of seconds."""
    base: dict[str, Any] = {
        "run.sites": 4,
        "run.seed": 11,
        "lot.trays": 1,
        "lot.parts_per_tray": 40,
        "handler.jam_prob": 0.2,
        "handler.contact_fail_prob": 0.2,
    }
    base.update(extra)
    return base


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Repository root."""
    return ROOT


@pytest.fixture(scope="session")
def configs_dir() -> Path:
    """Directory holding the example configurations."""
    return CONFIGS


@pytest.fixture()
def cp_session():
    """Small CP session (2 sites, 150 mm wafer, one wafer)."""
    from pyate.config.loader import load_session

    return load_session(CP_SESSION, small_cp_overrides())


@pytest.fixture()
def ft_session():
    """Small FT session (4 sites, one 40 part tray)."""
    from pyate.config.loader import load_session

    return load_session(FT_SESSION, small_ft_overrides())


@pytest.fixture()
def run_cp(tmp_path: Path):
    """Factory running a CP stage and returning ``(controller, summary)``."""
    from pyate.config.loader import load_session
    from pyate.engine.controller import RunController

    def _run(output: str = "out", sinks: list[Any] | None = None, **overrides: Any):
        session = load_session(CP_SESSION, small_cp_overrides(**overrides))
        controller = RunController(
            session,
            site_count=session.site_count(),
            output_dir=tmp_path / output,
            resume=False,
            sinks=list(sinks or []),
        )
        return controller, controller.run()

    return _run


@pytest.fixture()
def run_ft(tmp_path: Path):
    """Factory running an FT stage and returning ``(controller, summary)``."""
    from pyate.config.loader import load_session
    from pyate.engine.controller import RunController

    def _run(output: str = "out", sinks: list[Any] | None = None, **overrides: Any):
        session = load_session(FT_SESSION, small_ft_overrides(**overrides))
        controller = RunController(
            session,
            site_count=session.site_count(),
            output_dir=tmp_path / output,
            resume=False,
            sinks=list(sinks or []),
        )
        return controller, controller.run()

    return _run
