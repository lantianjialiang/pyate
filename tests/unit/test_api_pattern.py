"""Tests for the ``api.pattern`` facade (standalone pattern debugging)."""

from __future__ import annotations

import pytest

from pyate.api.pattern import available_models, default_profile, run_pattern

from conftest import CONFIGS

PATTERN = CONFIGS / "demo_cp" / "patterns" / "cnt_basic.pat"


def test_available_models_lists_the_builtins() -> None:
    models = available_models()
    assert {"counter", "shiftreg", "composite"} <= set(models)


def test_default_profile_is_defect_free() -> None:
    profile = default_profile("counter", bits=4)
    assert profile.model == "counter"
    assert profile.defects == []
    assert profile.yield_.pass_rate == 1.0
    assert profile.params["bits"] == 4


def test_pattern_passes_on_the_matching_model() -> None:
    report = run_pattern(PATTERN, model="composite", max_cycles=5000)
    assert report.passed is True
    assert report.fail_count == 0
    assert report.fail_pins == ()
    assert report.first_fail is None
    assert report.vectors == 21
    assert report.cycles == 21
    assert report.pattern == "cnt_basic"


def test_pattern_fails_on_the_wrong_model() -> None:
    report = run_pattern(PATTERN, model="counter", max_cycles=5000)
    assert report.passed is False
    assert report.first_fail is not None
    assert report.first_fail.cycle >= 1
    payload = report.to_dict()
    assert payload["passed"] is False
    assert payload["first_fail"]["pins"]


def test_stop_on_first_fail_can_be_disabled() -> None:
    first = run_pattern(PATTERN, model="counter", max_cycles=5000)
    everything = run_pattern(PATTERN, model="counter", max_cycles=5000,
                             stop_on_first_fail=False)
    assert everything.fail_count >= first.fail_count


def test_report_is_json_friendly() -> None:
    payload = run_pattern(PATTERN, model="composite", max_cycles=5000).to_dict()
    assert set(payload) == {"path", "pattern", "vectors", "cycles", "passed",
                            "elapsed_ms", "fail_count", "fail_pins", "first_fail"}


def test_unknown_model_is_reported() -> None:
    with pytest.raises(Exception):
        run_pattern(PATTERN, model="no_such_model", max_cycles=10)


def test_unknown_pattern_path_is_reported() -> None:
    with pytest.raises(Exception):
        run_pattern(CONFIGS / "demo_cp" / "patterns" / "missing.pat", model="composite")
