"""Unit tests for the configuration layer (schema, loader, validators)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyate.config import ConfigError, load_session, read_config, validate_session
from pyate.config.loader import deep_merge, split_overrides
from pyate.config.models import (
    BinningConfig,
    ChannelMapConfig,
    DutProfile,
    FlowNode,
    Limit,
    LimitsConfig,
    LevelsConfig,
    SessionSpec,
    TimingConfig,
)
from pyate.config.models import TestProgram as ProgramModel

from conftest import CONFIGS, CP_SESSION, FT_SESSION, small_cp_overrides


# --------------------------------------------------------------------- models
def test_channel_map_expands_a_bus() -> None:
    """A ``Q[0:7]`` entry becomes one bus pin with 8 channels per site."""
    config = ChannelMapConfig.model_validate({
        "pins": [{"pin": "Q[0:7]", "type": "out", "sites": {1: 10}}],
    })
    assert len(config.pins) == 1
    bus = config.pins[0]
    assert bus.pin == "Q"
    assert bus.width == 8
    assert bus.channel_of(1, 3) == 13
    expanded = config.expand()
    assert "Q0" in expanded and "Q7" in expanded
    assert expanded["Q5"] == (bus, 5)


def test_binning_maps_fail_codes() -> None:
    config = BinningConfig.model_validate({
        "pass_bin": 1,
        "default_fail_bin": 9,
        "soft_bins": [{"sbin": 1, "pass_fail": "P"}, {"sbin": 5, "pass_fail": "F"}],
        "hard_bins": [{"hbin": 1}, {"hbin": 5}],
        "fail_code_bins": {5: 5},
    })
    assert config.hard_bin_of(1) == 1
    assert config.hard_bin_of(5) == 5


def test_flow_node_requires_exactly_one_kind() -> None:
    with pytest.raises(Exception):
        FlowNode.model_validate({"item": "idd_static", "test_num": 2000, "group": "g"})
    with pytest.raises(Exception):
        FlowNode.model_validate({"item": "idd_static"})  # missing test_num
    node = FlowNode.model_validate({"item": "idd_static", "test_num": 2000})
    assert node.kind() == "item"


def test_program_stages_and_flow_lookup() -> None:
    program = ProgramModel.model_validate({
        "program": "P",
        "flows": {"CP1": [{"item": "continuity", "test_num": 1000}], "FT1": []},
    })
    assert program.stages() == ["CP1", "FT1"]
    assert program.flow("CP1")[0].item == "continuity"
    with pytest.raises(KeyError):
        program.flow("NOPE")


def test_limit_matching_by_stage_and_temperature() -> None:
    limit = Limit.model_validate({"test_num": 2000, "name": "idd", "lo": 0, "hi": 1e-3,
                                  "stage": "CP1", "temp_min": 0, "temp_max": 99})
    assert limit.matches("CP1", 25.0)
    assert not limit.matches("FT1", 25.0)
    assert not limit.matches("CP1", 125.0)
    wildcard = Limit.model_validate({"test_num": 1, "name": "x"})
    assert wildcard.matches("ANY", -40.0)


def test_levels_and_timing_defaults_are_usable() -> None:
    levels = LevelsConfig.model_validate({"sets": [{"name": "lv"}]})
    assert levels.get().vdd.nom == 3.3
    timing = TimingConfig.model_validate({"sets": [{"name": "t", "period_ns": 50}]})
    assert timing.get().edges.compare == 15.0


# --------------------------------------------------------------------- loader
def test_deep_merge_and_split_overrides() -> None:
    merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
    assert merged == {"a": {"b": 1, "c": 3}}
    session_level, sections = split_overrides({
        "run.seed": 7, "dut_profile.model": "counter", "name": "x",
    })
    assert session_level == {"run.seed": 7, "name": "x"}
    assert sections == {"dut_profile": {"model": "counter"}}


def test_read_config_supports_includes(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("a: 1\nb: 2\n", encoding="utf-8")
    child = tmp_path / "child.yaml"
    child.write_text("include: base.yaml\nb: 3\n", encoding="utf-8")
    data = read_config(child)
    assert data["a"] == 1
    assert data["b"] == 3
    assert "include" not in data


def test_read_config_expands_env_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYATE_TEST_VALUE", raising=False)
    path = tmp_path / "env.yaml"
    path.write_text("value: ${PYATE_TEST_VALUE:fallback}\n", encoding="utf-8")
    assert read_config(path)["value"] == "fallback"
    monkeypatch.setenv("PYATE_TEST_VALUE", "set")
    assert read_config(path)["value"] == "set"


def test_session_spec_requires_file_references(tmp_path: Path) -> None:
    spec = SessionSpec.model_validate({"name": "x"})
    assert spec.program == "testprogram.yaml"  # defaults are filled in
    with pytest.raises(Exception):
        SessionSpec.model_validate({"run": {"sites": "many"}})
    incomplete = tmp_path / "session.yaml"
    incomplete.write_text("name: broken\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_session(incomplete, validate=False)
    assert "not found" in str(excinfo.value)
    spec = SessionSpec.model_validate({
        "program": "p.yaml", "tester": "t.yaml", "channel_map": "c.yaml",
        "levels": "l.yaml", "timing": "ti.yaml", "binning": "b.yaml",
        "dut_profile": "d.yaml",
    })
    assert spec.run.stage == "CP1"


# ------------------------------------------------------------------- session
def test_demo_configs_load_and_validate() -> None:
    for path in (CP_SESSION, FT_SESSION):
        session = load_session(path)
        report = validate_session(session)
        assert report.ok, f"{path}: {report.format()}"


def test_section_override_reaches_referenced_file() -> None:
    session = load_session(CP_SESSION, {"dut_profile.model": "counter"})
    assert session.dut_profile.model == "counter"
    session = load_session(CP_SESSION, {"run.seed": 1234, "run.sites": 1})
    assert session.run.seed == 1234
    assert session.site_count() == 1


def test_limits_override_is_rejected() -> None:
    with pytest.raises(ConfigError):
        load_session(CP_SESSION, {"limits.foo": 1})


def test_invalid_model_name_fails_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_session(CP_SESSION, {"dut_profile.model": "definitely-not-a-model"})
    assert "unknown DUT model" in str(excinfo.value)


def test_limits_csv_rows_are_loaded() -> None:
    session = load_session(CP_SESSION)
    assert isinstance(session.limits, LimitsConfig)
    nums = {limit.test_num for limit in session.limits.limits}
    assert 2000 in nums


def test_resolved_session_exposes_helpers() -> None:
    session = load_session(CP_SESSION, small_cp_overrides())
    assert session.site_count() == 2
    assert session.temperature() > -273
    assert session.levels_for("lv_nominal").vdd.nom > 0
    assert "CLK" in session.pin_names()
    snapshot = session.to_snapshot()
    assert snapshot["name"] == "demo_cp"


def test_dut_profile_defaults() -> None:
    profile = DutProfile.model_validate({"model": "counter"})
    assert profile.yield_.pass_rate == 0.9
    assert profile.process.vth_sigma > 0


def test_output_dir_resolution(tmp_path: Path) -> None:
    session = load_session(CP_SESSION)
    assert session.output_dir.is_absolute()
    assert session.output_dir.name == "out"
