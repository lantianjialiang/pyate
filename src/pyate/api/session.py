"""API facade for a single session: load, validate, inspect and edit.

Only this package (and :mod:`pyate.engine`) is used by the CLI and the GUI; the
GUI never imports ``pyate.core`` internals such as the HAL or the flow executor.
"""

from __future__ import annotations

import copy
import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import ConfigError
from ..config.loader import LoadedSession, load_session
from ..config.models import (
    BinningConfig,
    ChannelMapConfig,
    DutProfile,
    FlowNode,
    HandlerConfig,
    LevelsConfig,
    Limit,
    LimitsConfig,
    LoadboardConfig,
    LotConfig,
    OnFail,
    ProberConfig,
    RunSpec,
    SessionSpec,
    StdfOptions,
    TesterConfig,
    TestProgram,
    TimingConfig,
    WaferConfig,
)
from ..config.validators import ValidationReport, validate_session

__all__ = ["Session", "SessionInfo", "ConfigProblem", "FileDocument", "SaveResult"]

_LIMIT_COLUMNS = [
    "test_num", "name", "lo", "hi", "unit", "scale", "stage", "temp_min", "temp_max",
]

#: File key -> attribute on :class:`LoadedSession` and the pydantic model.
_FILE_MODELS: dict[str, tuple[str, type[Any]]] = {
    "tester": ("tester", TesterConfig),
    "channel_map": ("channel_map", ChannelMapConfig),
    "loadboard": ("loadboard", LoadboardConfig),
    "levels": ("levels", LevelsConfig),
    "timing": ("timing", TimingConfig),
    "program": ("program", TestProgram),
    "limits": ("limits", LimitsConfig),
    "binning": ("binning", BinningConfig),
    "dut_profile": ("dut_profile", DutProfile),
    "wafer": ("wafer", WaferConfig),
    "lot": ("lot", LotConfig),
    "prober": ("prober", ProberConfig),
    "handler": ("handler", HandlerConfig),
    "stdf": ("stdf", StdfOptions),
    "run": ("run", RunSpec),
    "session": ("spec", SessionSpec),
}


class ConfigProblem(RuntimeError):
    """Raised when a session cannot be loaded (message is user facing)."""


@dataclass
class SaveResult:
    """Outcome of writing one configuration file."""

    ok: bool
    text: str
    path: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class FileDocument:
    """One configuration file, ready for a form and a text editor."""

    key: str
    path: str
    kind: str
    text: str
    data: Any
    schema: dict[str, Any]


@dataclass
class SessionInfo:
    """Read-only summary shown in the GUI header / CLI output."""

    name: str
    path: str
    stage: str
    sites: int
    temperature: float
    program: str
    dut_model: str
    stages: list[str] = field(default_factory=list)
    wafer: str = ""
    lot: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON friendly view."""
        return {
            "name": self.name, "path": self.path, "stage": self.stage, "sites": self.sites,
            "temperature": self.temperature, "program": self.program,
            "dut_model": self.dut_model, "stages": list(self.stages),
            "wafer": self.wafer, "lot": self.lot,
        }


class Session:
    """A loaded session plus the edits the GUI wants to make before running."""

    def __init__(self, path: str | Path, overrides: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.overrides: dict[str, Any] = dict(overrides or {})
        self._loaded: LoadedSession | None = None
        self._raw: dict[str, Any] | None = None

    # ------------------------------------------------------------------ load
    def load(self, *, force: bool = False) -> LoadedSession:
        """Load (and cache) the session, applying every pending override."""
        if self._loaded is not None and not force:
            return self._loaded
        try:
            self._loaded = load_session(self.path, self.overrides)
        except ConfigError as exc:
            raise ConfigProblem(str(exc)) from exc
        return self._loaded

    def reload(self) -> LoadedSession:
        """Reload from disk, keeping overrides."""
        return self.load(force=True)

    @property
    def loaded(self) -> LoadedSession:
        """Loaded session (loading on first use)."""
        return self.load()

    def validate(self) -> ValidationReport:
        """Validate the cached session without raising.

        Disk is read only when nothing is cached yet. In-memory edits (limits,
        flow) stay in place; ``set`` / ``reload`` drop the cache when a fresh
        read is required.
        """
        try:
            loaded = self.load()
        except ConfigProblem as exc:
            return ValidationReport(errors=[str(exc)])
        return validate_session(loaded)

    # ------------------------------------------------------------------ edit
    def set(self, key: str, value: Any) -> None:
        """Add a ``dotted.path=value`` override and drop the cached session."""
        self.overrides[str(key)] = value
        self._loaded = None

    def update(self, overrides: dict[str, Any]) -> None:
        """Add several overrides at once."""
        for key, value in overrides.items():
            self.set(key, value)

    def unset(self, key: str) -> None:
        """Remove an override."""
        self.overrides.pop(str(key), None)
        self._loaded = None

    # ------------------------------------------------------------------ info
    def info(self) -> SessionInfo:
        """Summary of the loaded session."""
        loaded = self.load()
        return SessionInfo(
            name=loaded.name,
            path=str(loaded.path),
            stage=loaded.stage,
            sites=loaded.site_count(),
            temperature=loaded.temperature(),
            program=loaded.program.program,
            dut_model=loaded.dut_profile.model,
            stages=loaded.program.stages(),
            wafer=getattr(getattr(loaded, "wafer", None), "wafer_id", "") or "",
            lot=getattr(getattr(loaded, "lot", None), "lot_id", "") or "",
        )

    def stages(self) -> list[str]:
        """Available stage names of the test program."""
        return list(self.load().program.stages())

    def flow(self, stage: str | None = None) -> list[dict[str, Any]]:
        """Nested, JSON friendly view of a flow (used by the GUI tree)."""
        loaded = self.load()
        stage_name = stage or loaded.stage
        try:
            nodes = loaded.program.flow(stage_name)
        except KeyError:
            return []
        return [self._node_dict(node) for node in nodes]

    @staticmethod
    def _node_dict(node: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": node.kind(),
            "label": node.item or node.group or (
                f"branch: {node.branch.if_}" if node.branch else (
                    f"loop x{node.loop.count}" if node.loop else f"goto bin {node.goto_bin}"
                )
            ),
            "test_num": node.test_num,
            "enabled": node.enabled,
            "params": copy.deepcopy(node.params),
        }
        if node.on_fail is not None:
            data["on_fail"] = node.on_fail.model_dump()
        if node.items:
            data["children"] = [Session._node_dict(child) for child in node.items]
        if node.branch is not None and node.branch.items:
            data.setdefault("children", []).extend(
                Session._node_dict(child) for child in node.branch.items
            )
        if node.loop is not None and node.loop.items:
            data.setdefault("children", []).extend(
                Session._node_dict(child) for child in node.loop.items
            )
        return data

    def items(self, stage: str | None = None) -> list[dict[str, Any]]:
        """Flat list of ``(test_num, name, enabled)`` triples for a flow."""
        out: list[dict[str, Any]] = []

        def walk(nodes: list[Any]) -> None:
            for node in nodes:
                if node.item is not None:
                    out.append({"test_num": node.test_num, "item": node.item,
                                "enabled": node.enabled,
                                "params": copy.deepcopy(node.params)})
                for child in list(node.items or []):
                    walk([child])
                if node.branch is not None:
                    walk(list(node.branch.items or []))
                if node.loop is not None:
                    walk(list(node.loop.items or []))

        loaded = self.load()
        try:
            walk(loaded.program.flow(stage or loaded.stage))
        except KeyError:
            return []
        return out

    def limits(self) -> list[dict[str, Any]]:
        """Limits table as a list of rows (editable in the GUI)."""
        return [limit.model_dump() for limit in self.load().limits.limits]

    def update_limit(self, test_num: int, *, lo: float | None = None,
                     hi: float | None = None) -> bool:
        """Rewrite one limit row in the loaded session.

        Returns ``False`` when no row matches ``test_num`` (nothing is changed).
        Values passed as ``None`` keep the current limit, which lets the GUI
        clear a limit by writing an explicit ``0`` or by editing the CSV.
        """
        from ..config.models import Limit, LimitsConfig

        loaded = self.load()
        rows: list[dict[str, Any]] = []
        changed = False
        for limit in loaded.limits.limits:
            data = limit.model_dump()
            if int(data["test_num"]) == int(test_num):
                if lo is not None:
                    data["lo"] = lo
                if hi is not None:
                    data["hi"] = hi
                changed = True
            rows.append(data)
        if not changed:
            return False
        loaded.limits = LimitsConfig(limits=[Limit.model_validate(row) for row in rows])
        return True

    def bins(self) -> dict[str, Any]:
        """Soft/hard bin tables."""
        binning = self.load().binning
        return {
            "pass_bin": binning.pass_bin,
            "default_fail_bin": binning.default_fail_bin,
            "soft_bins": [item.model_dump() for item in binning.soft_bins],
            "hard_bins": [item.model_dump() for item in binning.hard_bins],
            "rules": [item.model_dump() for item in binning.rules],
            "bin_priority": list(binning.bin_priority),
            "fail_code_bins": dict(binning.fail_code_bins),
        }

    def snapshot(self) -> dict[str, Any]:
        """Resolved configuration snapshot (``run_snapshot.yaml`` content)."""
        return self.load().to_snapshot()

    # ------------------------------------------------------------- file edit
    def file_keys(self) -> list[str]:
        """Keys the editor can open (referenced files, plus session and run)."""
        loaded = self.load()
        keys = sorted(loaded.files)
        for extra in ("run", "session"):
            if extra not in keys:
                keys.append(extra)
        return keys

    def file_document(self, key: str) -> FileDocument:
        """Structured view of one file. Raises when the session has no such file."""
        loaded = self.load()
        attr, model = self._model_of(key)
        obj = getattr(loaded, attr)
        if obj is None:
            raise ConfigProblem(f"{key} is not part of this session")
        data = obj.model_dump(mode="json", by_alias=True)
        path = self._path_of(loaded, key)
        kind = "csv" if key == "limits" and path.suffix.lower() == ".csv" else "yaml"
        text = _limits_to_csv(obj) if kind == "csv" else _dump_yaml(data)
        schema = model.model_json_schema(by_alias=True)
        return FileDocument(key=key, path=str(path), kind=kind, text=text,
                            data=data, schema=schema)

    def item_schema(self, name: str) -> dict[str, Any]:
        """JSON schema of a test item's parameter model."""
        from ..testitems.registry import get_testitem_class

        return get_testitem_class(name).Params.model_json_schema()

    def save_file_text(self, key: str, text: str) -> SaveResult:
        """Parse ``text`` (YAML or limits CSV) and save it if the session still validates."""
        loaded = self.load()
        path = self._path_of(loaded, key)
        try:
            if key == "limits" and path.suffix.lower() == ".csv":
                data: Any = {"limits": _parse_limits_csv(text)}
            else:
                data = yaml.safe_load(text) or {}
        except Exception as exc:  # noqa: BLE001 - shown in the editor
            return SaveResult(False, f"{key}: {exc}")
        return self.save_file_data(key, data)

    def save_file_data(self, key: str, data: Any) -> SaveResult:
        """Validate ``data`` as ``key`` and write the file. A failed check does not touch disk."""
        loaded = self.load()
        attr, model = self._model_of(key)
        try:
            obj = model.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - pydantic.ValidationError
            return SaveResult(False, f"{key}: {exc}")
        if key in ("session", "run"):
            return self._save_session_spec(loaded, key, obj)
        trial = copy.copy(loaded)
        setattr(trial, attr, obj)
        report = validate_session(trial)
        if not report.ok:
            return SaveResult(False, report.format())
        path = self._path_of(loaded, key)
        if key == "limits" and path.suffix.lower() == ".csv":
            payload = _limits_to_csv(obj)
        else:
            payload = _dump_yaml(obj.model_dump(mode="json", by_alias=True))
        _replace_text(path, payload)
        return self._reload_after_write(path)

    def export_limits_csv(self, path: str | Path) -> Path:
        """Write the current limits to ``path`` without changing the session file."""
        target = Path(path)
        _replace_text(target, _limits_to_csv(self.load().limits))
        return target

    # ------------------------------------------------------------------- flow
    def set_enabled(self, stage: str, path: list[int], enabled: bool) -> None:
        """Toggle ``FlowNode.enabled`` for the node at ``path``."""
        self._node_at(stage, path).enabled = bool(enabled)

    def set_item_params(self, stage: str, path: list[int], params: dict[str, Any]) -> None:
        """Replace one item's params after checking them against its schema."""
        from ..testitems.registry import get_testitem_class

        node = self._node_at(stage, path)
        if node.item is None:
            raise ConfigProblem("only a test item has parameters")
        get_testitem_class(str(node.item)).Params.model_validate(params)
        node.params = dict(params)

    def set_on_fail(self, stage: str, path: list[int], *, action: str,
                    bin: int | None, max_retries: int) -> None:
        """Replace the on-fail policy of one node."""
        node = self._node_at(stage, path)
        node.on_fail = OnFail(action=action, bin=bin, max_retries=int(max_retries))

    def move_flow(self, stage: str, path: list[int], delta: int) -> bool:
        """Swap a node with its sibling ``delta`` places away. Returns False at the edge."""
        if not path:
            return False
        parent_path = list(path[:-1])
        index = int(path[-1])
        current = self._child_list(stage, parent_path)
        new_index = index + int(delta)
        if new_index < 0 or new_index >= len(current):
            return False
        order = list(range(len(current)))
        order[index], order[new_index] = order[new_index], order[index]
        self.reorder_flow(stage, parent_path, order)
        return True

    def reorder_flow(self, stage: str, parent_path: list[int], order: list[int]) -> None:
        """Permute the children of ``parent_path`` (empty path = the stage root)."""
        current = self._child_list(stage, parent_path)
        wanted = [int(index) for index in order]
        if sorted(wanted) != list(range(len(current))):
            raise ConfigProblem("reorder must list every sibling exactly once")
        current[:] = [current[index] for index in wanted]

    def save_program(self) -> SaveResult:
        """Write ``testprogram.yaml`` if the edited flow still validates."""
        data = self.load().program.model_dump(mode="json", by_alias=True)
        return self.save_file_data("program", data)

    def save_binning(self, data: dict[str, Any]) -> SaveResult:
        """Write ``binning.yaml`` from an edited bin table."""
        return self.save_file_data("binning", data)

    # ---------------------------------------------------------------- helpers
    def _model_of(self, key: str) -> tuple[str, type[Any]]:
        try:
            return _FILE_MODELS[key]
        except KeyError as exc:
            known = ", ".join(sorted(_FILE_MODELS))
            raise ConfigProblem(f"unknown config file '{key}' (known: {known})") from exc

    def _path_of(self, loaded: LoadedSession, key: str) -> Path:
        if key in ("session", "run"):
            return loaded.path
        raw = loaded.files.get(key)
        if not raw:
            raise ConfigProblem(f"{key} is not part of this session")
        return Path(raw)

    def _save_session_spec(self, loaded: LoadedSession, key: str, obj: Any) -> SaveResult:
        if key == "run":
            spec = loaded.spec.model_copy(update={"run": obj})
        else:
            spec = obj
        path = loaded.path
        tmp = path.with_name(path.name + ".pyate-tmp")
        _replace_text(tmp, _dump_yaml(spec.model_dump(mode="json", by_alias=True)))
        try:
            load_session(tmp)
        except ConfigError as exc:
            tmp.unlink(missing_ok=True)
            return SaveResult(False, str(exc))
        tmp.replace(path)
        return self._reload_after_write(path)

    def _reload_after_write(self, path: Path) -> SaveResult:
        self._loaded = None
        try:
            self.load(force=True)
        except ConfigProblem as exc:
            return SaveResult(False, str(exc), str(path))
        return SaveResult(True, f"saved {path}", str(path))

    def _node_at(self, stage: str, path: list[int]) -> FlowNode:
        if not path:
            raise ConfigProblem("missing flow node path")
        current = self._child_list(stage, [])
        node = current[int(path[0])]
        for index in path[1:]:
            current = _children_of(node)
            node = current[int(index)]
        return node

    def _child_list(self, stage: str, parent_path: list[int]) -> list[Any]:
        flows = self.load().program.flows
        if stage not in flows:
            raise ConfigProblem(f"stage '{stage}' is not in the test program")
        if not parent_path:
            return flows[stage]
        return _children_of(self._node_at(stage, parent_path))


def _children_of(node: FlowNode) -> list[Any]:
    """The list a reorder operates on (group items, loop body, or branch-then)."""
    if node.group is not None:
        return node.items
    if node.loop is not None:
        return node.loop.items
    if node.branch is not None:
        return node.branch.then
    raise ConfigProblem("this flow node has no children to reorder")


def _dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _replace_text(path: Path, text: str) -> None:
    """Write ``text`` via a temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".pyate-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _limits_to_csv(limits: LimitsConfig) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_LIMIT_COLUMNS)
    for limit in limits.limits:
        writer.writerow([
            limit.test_num, limit.name,
            "" if limit.lo is None else limit.lo,
            "" if limit.hi is None else limit.hi,
            limit.unit, limit.scale, limit.stage, limit.temp_min, limit.temp_max,
        ])
    return buffer.getvalue()


def _parse_limits_csv(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        clean = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
        if not clean.get("test_num"):
            continue
        row = {
            "test_num": int(clean["test_num"]),
            "name": clean.get("name", ""),
            "lo": float(clean["lo"]) if clean.get("lo") else None,
            "hi": float(clean["hi"]) if clean.get("hi") else None,
            "unit": clean.get("unit", ""),
            "scale": int(clean["scale"]) if clean.get("scale") else 0,
            "stage": clean.get("stage") or "*",
            "temp_min": float(clean["temp_min"]) if clean.get("temp_min") else -999.0,
            "temp_max": float(clean["temp_max"]) if clean.get("temp_max") else 999.0,
        }
        Limit.model_validate(row)
        rows.append(row)
    return rows
