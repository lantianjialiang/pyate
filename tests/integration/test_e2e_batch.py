"""E2E-BATCH: sweep a batch, then check the aggregated capability report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyate.batch import BatchRunner, expand_jobs, load_batch

from conftest import CONFIGS

BATCH = CONFIGS / "demo_batch" / "batch.yaml"


@pytest.fixture(scope="module")
def batch_run(tmp_path_factory):
    """Run the first two jobs of the demo sweep (enough for aggregation)."""
    spec, jobs = load_batch(BATCH)
    jobs = expand_jobs(spec)[:2]
    output = tmp_path_factory.mktemp("batch")
    summary = BatchRunner(spec, jobs, output_dir=output).run()
    return spec, summary, Path(output)


def test_every_job_succeeded(batch_run) -> None:
    spec, summary, output = batch_run
    assert len(summary.jobs) == 2
    assert not summary.failed
    assert summary.counts.get("DONE") == 2
    assert summary.duration_s >= 0


def test_each_job_reports_parts_and_stdf(batch_run) -> None:
    spec, summary, output = batch_run
    for job in summary.jobs:
        assert job.tested > 0
        assert 0.0 < job.yield_pct <= 100.0
        assert job.stdf_files
        for path in job.stdf_files:
            assert Path(path).is_file()


def test_job_overrides_are_visible_in_the_ids(batch_run) -> None:
    spec, summary, output = batch_run
    ids = [job.job_id for job in summary.jobs]
    assert len(set(ids)) == 2
    assert all("job" in job_id for job_id in ids)


def test_aggregate_report_has_yield_and_capability(batch_run) -> None:
    spec, summary, output = batch_run
    data = summary.to_dict()
    aggregate = data["aggregate"]
    assert aggregate["tested"] > 0
    assert 0.0 < aggregate["yield_pct"] <= 100.0
    assert aggregate["uph_avg"] > 0
    assert aggregate["bin_counts"]
    assert aggregate["bin_pareto"]
    assert aggregate["capability"], "Cpk rows must be aggregated"
    row = aggregate["capability"][0]
    for key in ("test_num", "name", "count", "mean", "sigma", "min", "max",
                "lo", "hi", "cp", "cpk"):
        assert key in row
    assert row["count"] > 0
    assert row["sigma"] >= 0
    assert row["min"] <= row["mean"] <= row["max"]


def test_batch_artefacts_exist(batch_run) -> None:
    spec, summary, output = batch_run
    assert (output / "batch_summary.json").is_file()
    assert (output / "batch_summary.csv").is_file()
    assert (output / "batch_report.md").is_file()
    payload = json.loads((output / "batch_summary.json").read_text(encoding="utf-8"))
    assert payload["batch_id"] == spec.batch_id
    assert len(payload["jobs"]) == 2
    lines = (output / "batch_summary.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # header + two jobs


def test_bin_pareto_adds_up_to_the_tested_parts(batch_run) -> None:
    spec, summary, output = batch_run
    aggregate = summary.to_dict()["aggregate"]
    total = sum(entry["count"] for entry in aggregate["bin_pareto"])
    assert total == aggregate["tested"]


def test_job_summary_is_reloadable(batch_run) -> None:
    spec, summary, output = batch_run
    payload = json.loads((output / "batch_summary.json").read_text(encoding="utf-8"))
    first = payload["jobs"][0]
    for key in ("job_id", "status", "tested", "passed", "yield_pct", "stdf_files"):
        assert key in first


def test_spec_sweep_produces_the_documented_grid() -> None:
    spec, jobs = load_batch(BATCH)
    expanded = expand_jobs(spec)
    temps = {job.overrides["run.temperature"] for job in expanded}
    profiles = {job.overrides["dut_profile.yield.pass"] for job in expanded}
    assert len(temps) == 3
    assert len(profiles) == 2
    assert len(expanded) == 6
