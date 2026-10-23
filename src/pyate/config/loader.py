"""Configuration loading: YAML includes, ``${ENV}`` expansion, dot overrides.

Every path is resolved relative to the session file, so a config directory is
portable as a whole.  Loaded models are validated by :mod:`pyate.config.validators`.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import (
    BinningConfig,
    ChannelMapConfig,
    DutProfile,
    HandlerConfig,
    LevelsConfig,
    Limit,
    LimitsConfig,
    LoadboardConfig,
    LotConfig,
    ProberConfig,
    RunSpec,
    SessionSpec,
    SimulationSpec,
    StdfOptions,
    TesterConfig,
    TestProgram,
    TimingConfig,
    WaferConfig,
)

__all__ = ["ConfigError", "LoadedSession", "load_session", "read_config", "deep_merge"]

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


class ConfigError(Exception):
    """Raised when a configuration file cannot be read or validated."""

    def __init__(self, messages: str | list[str]) -> None:
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__("\n".join(self.messages))


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``extra`` into ``base`` (returns a new dict)."""
    out = dict(base)
    for key, value in extra.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        elif key in out and isinstance(out[key], list) and isinstance(value, list):
            out[key] = list(out[key]) + list(value)
        else:
            out[key] = value
    return out


def _expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:default}`` recursively."""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else match.group(0))

        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {key: _expand_env(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def read_config(path: str | Path) -> dict[str, Any]:
    """Read one YAML file with environment expansion and ``include:`` support."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigError(f"{file_path}: file not found")
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file_path}: invalid YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{file_path}: top level must be a mapping")
    data = _expand_env(raw)
    includes = data.pop("include", None) or data.pop("includes", None) or []
    if isinstance(includes, str):
        includes = [includes]
    merged: dict[str, Any] = {}
    for inc in includes:
        inc_path = (file_path.parent / inc).resolve()
        merged = deep_merge(merged, read_config(inc_path))
    return deep_merge(merged, data)


#: Session fields that are backed by a separate config file.
SECTIONS: frozenset[str] = frozenset({
    "program", "tester", "channel_map", "loadboard", "levels", "timing", "limits",
    "binning", "dut_profile", "wafer", "lot", "prober", "handler", "stdf",
})


def split_overrides(
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Split ``--set`` overrides into session-level and per-section overrides.

    ``run.seed=1`` targets the session itself. ``dut_profile.model=counter`` targets
    the ``dut_profile.yaml`` file: the first segment names a :data:`SECTIONS` entry
    and the rest is applied to that file's content before validation.
    """
    session_level: dict[str, Any] = {}
    sections: dict[str, dict[str, Any]] = {}
    for key, value in overrides.items():
        head, _, tail = str(key).partition(".")
        if head in SECTIONS and tail:
            sections.setdefault(head, {})[tail] = value
        else:
            session_level[key] = value
    return session_level, sections


def _apply_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply ``{"run.seed": 1}`` style overrides to a nested dict."""
    out = dict(data)
    for dotted, value in overrides.items():
        parts = dotted.split(".")
        cursor: dict[str, Any] = out
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = value
    return out


def _load_yaml_model(path: Path, model: type[Any], label: str) -> Any:
    data = read_config(path)
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(f"{label} ({path}):\n{exc}") from exc


def _load_limits(path: Path) -> LimitsConfig:
    """Load limits from CSV (preferred) or YAML."""
    if path.suffix.lower() == ".csv":
        rows: list[Limit] = []
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=2):
                clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                if not clean.get("test_num"):
                    continue
                try:
                    rows.append(
                        Limit(
                            test_num=int(clean["test_num"]),
                            name=clean.get("name", ""),
                            lo=float(clean["lo"]) if clean.get("lo") not in (None, "") else None,
                            hi=float(clean["hi"]) if clean.get("hi") not in (None, "") else None,
                            unit=clean.get("unit", ""),
                            scale=int(clean["scale"]) if clean.get("scale") else 0,
                            stage=clean.get("stage") or "*",
                            temp_min=float(clean["temp_min"]) if clean.get("temp_min") else -999.0,
                            temp_max=float(clean["temp_max"]) if clean.get("temp_max") else 999.0,
                        )
                    )
                except (KeyError, ValueError) as exc:
                    raise ConfigError(f"{path}:{index}: bad limit row: {exc}") from exc
        return LimitsConfig(limits=rows)
    return _load_yaml_model(path, LimitsConfig, "limits")


@dataclass
class LoadedSession:
    """A fully resolved session: every referenced config file has been loaded."""

    path: Path
    spec: SessionSpec
    tester: TesterConfig
    channel_map: ChannelMapConfig
    levels: LevelsConfig
    timing: TimingConfig
    binning: BinningConfig
    program: TestProgram
    dut_profile: DutProfile
    limits: LimitsConfig
    stdf: StdfOptions
    run: RunSpec
    sim: SimulationSpec
    loadboard: LoadboardConfig | None = None
    wafer: WaferConfig | None = None
    lot: LotConfig | None = None
    prober: ProberConfig | None = None
    handler: HandlerConfig | None = None
    files: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ api
    @property
    def root(self) -> Path:
        """Directory of the session file."""
        return self.path.parent

    @property
    def name(self) -> str:
        """Session name."""
        return self.spec.name

    @property
    def stage(self) -> str:
        """Test stage of this run (CP1/CP2/FT1 ...)."""
        return self.run.stage

    @property
    def output_dir(self) -> Path:
        """Resolved output directory."""
        raw = self.run.output_dir or self.stdf.output_dir
        path = Path(raw)
        return path if path.is_absolute() else (self.root / path).resolve()

    def levels_for(self, name: str | None = None) -> Any:
        """Resolve a named level set."""
        return self.levels.get(name)

    def timing_for(self, name: str | None = None) -> Any:
        """Resolve a named timing set."""
        return self.timing.get(name)

    def pin_names(self) -> list[str]:
        """Every expanded pin declared in the channel map."""
        return list(self.channel_map.expand())

    def site_count(self) -> int:
        """Number of sites to instantiate for this run."""
        if self.run.sites:
            return self.run.sites
        if self.loadboard is not None:
            return self.loadboard.site_count
        if self.prober is not None:
            return self.prober.layout.count
        if self.handler is not None:
            return self.handler.layout.count
        return min(4, self.tester.num_sites_max)

    def temperature(self) -> float:
        """Run temperature (run override > prober/handler > 25C)."""
        if self.run.temperature is not None:
            return self.run.temperature
        if self.prober is not None:
            return self.prober.temperature
        if self.handler is not None:
            return self.handler.temperature
        return 25.0

    def to_snapshot(self) -> dict[str, Any]:
        """Serialise the whole resolved configuration for reproducibility."""
        payload: dict[str, Any] = {
            "name": self.spec.name,
            "session": self.spec.model_dump(mode="json", by_alias=True),
            "tester": self.tester.model_dump(mode="json"),
            "channel_map": self.channel_map.model_dump(mode="json"),
            "levels": self.levels.model_dump(mode="json"),
            "timing": self.timing.model_dump(mode="json"),
            "binning": self.binning.model_dump(mode="json"),
            "program": self.program.model_dump(mode="json", by_alias=True),
            "dut_profile": self.dut_profile.model_dump(mode="json", by_alias=True),
            "limits": self.limits.model_dump(mode="json"),
            "stdf": self.stdf.model_dump(mode="json"),
            "run": self.run.model_dump(mode="json"),
            "sim": self.sim.model_dump(mode="json"),
            "files": dict(self.files),
        }
        for key in ("loadboard", "wafer", "lot", "prober", "handler"):
            obj = getattr(self, key)
            if obj is not None:
                payload[key] = obj.model_dump(mode="json")
        return payload


def load_session(
    path: str | Path,
    overrides: dict[str, Any] | None = None,
    *,
    validate: bool = True,
) -> LoadedSession:
    """Load and (optionally) validate a ``session.yaml``."""
    session_path = Path(path).resolve()
    session_over, section_over = split_overrides(overrides or {})
    raw = _apply_overrides(read_config(session_path), session_over)
    try:
        spec = SessionSpec.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"session ({session_path}):\n{exc}") from exc

    root = session_path.parent
    files: dict[str, str] = {}

    def resolve(rel: str | None) -> Path | None:
        if not rel:
            return None
        candidate = Path(rel)
        return candidate if candidate.is_absolute() else (root / candidate).resolve()

    def load(rel: str, model: type[Any], label: str) -> Any:
        target = resolve(rel)
        assert target is not None
        files[label] = str(target)
        data = read_config(target)
        if label in section_over:
            data = _apply_overrides(data, section_over[label])
        try:
            return model.model_validate(data)
        except Exception as exc:  # pydantic.ValidationError
            raise ConfigError(f"{label} ({target}):\n{exc}") from exc

    missing = [
        name for name in ("tester", "channel_map", "levels", "timing", "binning",
                          "program", "dut_profile")
        if not getattr(spec, name)
    ]
    if missing:
        raise ConfigError(f"session: missing required file reference(s): {', '.join(missing)}")

    tester = load(spec.tester, TesterConfig, "tester")
    channel_map = load(spec.channel_map, ChannelMapConfig, "channel_map")
    levels = load(spec.levels, LevelsConfig, "levels")
    timing = load(spec.timing, TimingConfig, "timing")
    binning = load(spec.binning, BinningConfig, "binning")
    program = load(spec.program, TestProgram, "program")
    dut_profile = load(spec.dut_profile, DutProfile, "dut_profile")

    if "limits" in section_over:
        raise ConfigError(
            "limits overrides are not supported (limits.csv rows are addressed by "
            "test_num/name); edit the limits file or use a different session"
        )
    limits_path = resolve(spec.limits)
    if limits_path is None:
        limits = LimitsConfig()
    else:
        if not limits_path.is_file():
            raise ConfigError(f"limits ({limits_path}): file not found")
        files["limits"] = str(limits_path)
        limits = _load_limits(limits_path)

    loadboard = load(spec.loadboard, LoadboardConfig, "loadboard") if spec.loadboard else None
    wafer = load(spec.wafer, WaferConfig, "wafer") if spec.wafer else None
    lot = load(spec.lot, LotConfig, "lot") if spec.lot else None
    prober = load(spec.prober, ProberConfig, "prober") if spec.prober else None
    handler = load(spec.handler, HandlerConfig, "handler") if spec.handler else None
    stdf = load(spec.stdf, StdfOptions, "stdf") if spec.stdf else StdfOptions()

    session = LoadedSession(
        path=session_path,
        spec=spec,
        tester=tester,
        channel_map=channel_map,
        levels=levels,
        timing=timing,
        binning=binning,
        program=program,
        dut_profile=dut_profile,
        limits=limits,
        stdf=stdf,
        run=spec.run,
        sim=tester.simulation,
        loadboard=loadboard,
        wafer=wafer,
        lot=lot,
        prober=prober,
        handler=handler,
        files=files,
    )
    if validate:
        from .validators import ValidationReport, validate_session

        report: ValidationReport = validate_session(session)
        if report.errors:
            raise ConfigError([str(session_path)] + report.errors)
    return session


def write_snapshot(session: LoadedSession, directory: str | Path) -> Path:
    """Write ``run_snapshot.yaml`` for reproducibility; returns its path."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "run_snapshot.yaml"
    target.write_text(
        yaml.safe_dump(session.to_snapshot(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target
