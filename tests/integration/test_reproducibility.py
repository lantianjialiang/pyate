"""E2E-REPRO: the same session + seed must reproduce the STDF bit for bit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyate.api import RunOptions, create_run
from pyate.datalog.stdf.records import iter_records

from conftest import CP_SESSION, FT_SESSION, small_cp_overrides, small_ft_overrides

#: Fields that legitimately change between two runs of the same simulation.
VOLATILE = {
    "MIR": {"SETUP_T", "START_T", "DATE_COD"},
    "MRR": {"FINISH_T"},
    "ATR": {"MOD_TIM"},
    "WIR": {"START_T"},
    "WRR": {"FINISH_T"},
    "PRR": {"TEST_T"},
    "TSR": {"TEST_TIM", "TEST_MIN", "TEST_MAX", "TST_SUMS", "TST_SQRS"},
}


def _normalised(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Decode an STDF file and blank the timestamp fields."""
    out: list[tuple[str, dict[str, Any]]] = []
    for name, values in iter_records(path.read_bytes()):
        stripped = dict(values)
        for field in VOLATILE.get(name, ()):  # keep structure, drop the clock
            if field in stripped:
                stripped[field] = None
        out.append((name, stripped))
    return out


def _run(session, overrides, output) -> tuple[Path, Any]:
    handle = create_run(session, overrides, RunOptions(output_dir=output))
    handle.prepare()
    summary = handle.run()
    return sorted(Path(output).glob("*.stdf"))[0], summary


def test_cp_stdf_is_identical_except_for_the_clock_fields(tmp_path) -> None:
    first, first_summary = _run(CP_SESSION, small_cp_overrides(), tmp_path / "a")
    second, second_summary = _run(CP_SESSION, small_cp_overrides(), tmp_path / "b")
    assert first_summary.bin_counts == second_summary.bin_counts
    assert len(_normalised(first)) == len(_normalised(second))
    assert [record for record in _normalised(first)] == [record for record in _normalised(second)]


def test_cp_stdf_records_match_field_by_field(tmp_path) -> None:
    first, _ = _run(CP_SESSION, small_cp_overrides(), tmp_path / "a")
    second, _ = _run(CP_SESSION, small_cp_overrides(), tmp_path / "b")
    left = _normalised(first)
    right = _normalised(second)
    assert len(left) == len(right)
    for index, ((ln, lv), (rn, rv)) in enumerate(zip(left, right)):
        assert ln == rn, f"record {index} type differs"
        assert lv == rv, f"record {index} ({ln}) differs"


def test_ft_stdf_is_reproducible(tmp_path) -> None:
    overrides = small_ft_overrides(**{"run.retest_bins": [2], "run.max_retries": 1})
    first, first_summary = _run(FT_SESSION, overrides, tmp_path / "a")
    second, second_summary = _run(FT_SESSION, overrides, tmp_path / "b")
    assert _normalised(first) == _normalised(second)
    assert first_summary.meta.get("retests") == second_summary.meta.get("retests")
    assert first_summary.bin_counts == second_summary.bin_counts


def test_a_different_seed_produces_a_different_die_map(tmp_path) -> None:
    first, _ = _run(CP_SESSION, small_cp_overrides(), tmp_path / "a")
    other, _ = _run(CP_SESSION, small_cp_overrides(**{"run.seed": 999}), tmp_path / "b")
    assert first.read_bytes() != other.read_bytes()


def test_summary_json_is_reproducible_apart_from_timing(tmp_path) -> None:
    import json

    _run(CP_SESSION, small_cp_overrides(), tmp_path / "a")
    _run(CP_SESSION, small_cp_overrides(), tmp_path / "b")
    left = json.loads((tmp_path / "a" / "summary.json").read_text(encoding="utf-8"))
    right = json.loads((tmp_path / "b" / "summary.json").read_text(encoding="utf-8"))
    for payload in (left, right):
        for key in ("started_at", "finished_at", "duration_s", "uph", "files"):
            payload.pop(key, None)
        for unit in payload.get("units", []):
            unit.pop("test_time_ms", None)
            unit.pop("uph", None)
            unit.pop("wall_ms", None)
    assert left == right
