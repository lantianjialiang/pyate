"""E2E-CP: a full CP run produces consistent STDF, wafer map and summary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pyate.api import EventKind, RunOptions, Session, create_run
from pyate.datalog.stdf.reader_check import verify_stdf

from conftest import CP_SESSION, small_cp_overrides


@pytest.fixture(scope="module")
def cp_run(tmp_path_factory):
    """One shared CP run (module scoped: a real run takes a second or two)."""
    output = tmp_path_factory.mktemp("cp_out")
    session = Session(CP_SESSION, small_cp_overrides())
    handle = create_run(CP_SESSION, small_cp_overrides(),
                        RunOptions(output_dir=output))
    handle.prepare()
    summary = handle.run()
    return session, handle, summary, Path(output)


def test_summary_reports_one_wafer(cp_run) -> None:
    session, handle, summary, output = cp_run
    assert summary.stage == "CP1"
    assert summary.seed == 42
    assert summary.total_parts > 0
    assert summary.total_tested == summary.total_parts
    assert 0.0 < summary.yield_pct < 100.0
    assert summary.uph > 0
    assert summary.aborted is False


def test_bin_counts_add_up(cp_run) -> None:
    session, handle, summary, output = cp_run
    assert sum(summary.bin_counts.values()) == summary.total_parts
    assert summary.bin_counts.get(1, 0) == summary.total_passed


def test_every_expected_artefact_exists(cp_run) -> None:
    session, handle, summary, output = cp_run
    assert (output / "summary.json").is_file()
    assert (output / "run_snapshot.yaml").is_file()
    assert (output / "W01_summary.json").is_file()
    assert (output / "W01_binmap.csv").is_file()
    assert (output / "W01_binmap.svg").is_file()
    assert (output / "W01_binmap.txt").is_file()
    assert list(output.glob("*.stdf"))


def test_stdf_passes_structural_verification(cp_run) -> None:
    session, handle, summary, output = cp_run
    stdf = sorted(output.glob("*.stdf"))[0]
    report = verify_stdf(stdf)
    assert report.ok, report.errors[:3]
    assert report.parts == summary.total_parts
    assert report.ptr_count > 0
    assert report.ftr_count > 0
    assert report.record_counts["FAR"] == 1
    assert report.record_counts["MIR"] == 1
    assert report.record_counts["MRR"] == 1
    assert report.pystdf_ok in (True, None)


def test_wafer_map_bins_match_the_stdf_bins(cp_run) -> None:
    session, handle, summary, output = cp_run
    rows = list(csv.DictReader((output / "W01_binmap.csv").open(encoding="utf-8")))
    assert rows
    bins: dict[int, int] = {}
    for row in rows:
        soft = int(row["bin"])
        if soft == 0:  # untested die (outside the wafer / not probed)
            continue
        bins[soft] = bins.get(soft, 0) + 1
    assert bins == {int(k): v for k, v in summary.bin_counts.items()}


def test_wafer_summary_is_consistent_with_the_run(cp_run) -> None:
    session, handle, summary, output = cp_run
    data = json.loads((output / "W01_summary.json").read_text(encoding="utf-8"))
    assert data["unit_id"] == "W01"
    assert data["kind"] == "wafer"
    assert data["tested"] == summary.total_tested
    assert data["passed"] == summary.total_passed
    assert data["parts"] == data["tested"]
    assert data["uph"] > 0
    assert sum(data["bin_counts"].values()) == data["parts"]


def test_event_stream_covers_the_whole_lot(tmp_path) -> None:
    from pyate.api import EventBus

    bus = EventBus()
    kinds: set[EventKind] = set()
    parts: list[str] = []
    bus.subscribe("*", lambda event: kinds.add(event.kind))
    bus.subscribe(EventKind.PART_END, lambda event: parts.append(event.data["part"]
                                                                 ["part_id"]))
    handle = create_run(CP_SESSION, small_cp_overrides(),
                        RunOptions(output_dir=tmp_path), events=bus)
    handle.prepare()
    summary = handle.run()
    for expected in (EventKind.LOT_START, EventKind.WAFER_START, EventKind.TOUCHDOWN,
                     EventKind.TEST_RESULT, EventKind.PART_END, EventKind.LOT_END):
        assert expected in kinds, expected
    assert len(parts) == summary.total_parts


def test_rerun_is_reproducible(cp_run, tmp_path) -> None:
    session, handle, summary, output = cp_run
    again = create_run(CP_SESSION, small_cp_overrides(), RunOptions(output_dir=tmp_path))
    again.prepare()
    other = again.run()
    assert other.total_parts == summary.total_parts
    assert other.total_passed == summary.total_passed
    assert other.bin_counts == summary.bin_counts


def test_changing_the_seed_changes_the_die_map(cp_run, tmp_path) -> None:
    session, handle, summary, output = cp_run
    other = create_run(CP_SESSION, small_cp_overrides(**{"run.seed": 4242}),
                       RunOptions(output_dir=tmp_path))
    other.prepare()
    seeded = other.run()
    assert seeded.total_parts == summary.total_parts
    assert seeded.bin_counts != summary.bin_counts


def test_site_count_override_is_honoured(cp_run, tmp_path) -> None:
    session, handle, summary, output = cp_run
    single = create_run(CP_SESSION, small_cp_overrides(**{"run.sites": 1}),
                        RunOptions(output_dir=tmp_path))
    single.prepare()
    one_site = single.run()
    assert one_site.total_parts == summary.total_parts  # same die count
    assert one_site.uph < summary.uph  # but slower


def test_only_restricts_the_executed_items(cp_run, tmp_path) -> None:
    session, handle, summary, output = cp_run
    limited = create_run(CP_SESSION, small_cp_overrides(),
                         RunOptions(output_dir=tmp_path, only=["continuity"]))
    limited.prepare()
    result = limited.run()
    assert result.total_parts == summary.total_parts
    short = verify_stdf(sorted(Path(tmp_path).glob("*.stdf"))[0])
    full = verify_stdf(sorted(output.glob("*.stdf"))[0])
    assert short.ptr_count < full.ptr_count
