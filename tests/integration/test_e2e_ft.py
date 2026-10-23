"""E2E-FT: handler run with jam / contact-fail injection and retest recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyate.api import EventKind, EventBus, RunOptions, create_run
from pyate.datalog.stdf.reader_check import verify_stdf

from conftest import FT_SESSION, small_ft_overrides

RETEST = {"run.retest_bins": [2], "run.max_retries": 1}


@pytest.fixture(scope="module")
def ft_runs(tmp_path_factory):
    """Two FT runs: without retest (baseline) and with contact-fail retest."""
    base_dir = tmp_path_factory.mktemp("ft_base")
    retest_dir = tmp_path_factory.mktemp("ft_retest")
    baseline = create_run(FT_SESSION, small_ft_overrides(**{"run.max_retries": 0}),
                          RunOptions(output_dir=base_dir))
    baseline.prepare()
    base_summary = baseline.run()
    recovered = create_run(FT_SESSION, small_ft_overrides(**RETEST),
                           RunOptions(output_dir=retest_dir))
    recovered.prepare()
    retest_summary = recovered.run()
    return (base_summary, Path(base_dir)), (retest_summary, Path(retest_dir))


def test_tray_run_tests_every_loaded_part(ft_runs) -> None:
    (base_summary, output), _ = ft_runs
    assert base_summary.stage == "FT1"
    assert base_summary.total_parts == 40
    assert base_summary.total_tested == base_summary.total_parts
    assert sum(base_summary.bin_counts.values()) == base_summary.total_parts


def test_contact_failures_produce_a_fail_bin(ft_runs) -> None:
    (base_summary, output), _ = ft_runs
    assert base_summary.bin_counts.get(2, 0) > 0, "contact fail injection must show up"
    assert base_summary.yield_pct < 100.0


def test_retest_improves_the_yield(ft_runs) -> None:
    (base_summary, base_out), (retest_summary, retest_out) = ft_runs
    assert retest_summary.total_parts == base_summary.total_parts
    assert retest_summary.yield_pct > base_summary.yield_pct
    assert retest_summary.bin_counts.get(2, 0) < base_summary.bin_counts.get(2, 0)
    assert retest_summary.meta.get("retests", 0) > 0


def test_stdf_of_the_retest_run_is_valid_and_flags_retested_parts(ft_runs) -> None:
    (base_summary, base_out), (retest_summary, retest_out) = ft_runs
    stdf = sorted(retest_out.glob("*.stdf"))[0]
    report = verify_stdf(stdf)
    assert report.ok, report.errors[:3]
    assert report.retest_parts > 0
    assert report.parts >= retest_summary.total_parts  # retries are appended too
    baseline = verify_stdf(sorted(base_out.glob("*.stdf"))[0])
    assert baseline.ok
    assert baseline.retest_parts == 0


def test_jam_recovery_is_reported(ft_runs) -> None:
    (base_summary, output), _ = ft_runs
    unit = base_summary.units[0]
    assert unit.kind == "tray"
    assert unit.meta["touchdowns"] >= 1
    assert unit.uph > 0


def test_tray_artefacts_are_written(ft_runs) -> None:
    (base_summary, output), _ = ft_runs
    assert (output / "summary.json").is_file()
    assert (output / "T01_summary.json").is_file()


def test_ft_events_carry_part_ids(tmp_path) -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(EventKind.PART_END,
                  lambda event: seen.append(event.data["part"]["part_id"]))
    handle = create_run(FT_SESSION, small_ft_overrides(), RunOptions(output_dir=tmp_path),
                        events=bus)
    handle.prepare()
    summary = handle.run()
    assert len(seen) >= 1
    assert len(seen) >= summary.total_parts
    assert all(part_id.startswith(summary.lot_id) for part_id in seen)


def test_ft_is_reproducible_for_the_same_seed(ft_runs, tmp_path) -> None:
    (base_summary, output), _ = ft_runs
    again = create_run(FT_SESSION, small_ft_overrides(**{"run.max_retries": 0}),
                       RunOptions(output_dir=tmp_path))
    again.prepare()
    other = again.run()
    assert other.bin_counts == base_summary.bin_counts
    assert other.total_passed == base_summary.total_passed
