"""Qt-free view model for the configuration / flow / limits editors."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ...api import ConfigProblem, Session

__all__ = ["ConfigViewModel", "ValidationResult"]


class ValidationResult:
    """Outcome of a configuration check (formatted for a status bar)."""

    def __init__(self, ok: bool, text: str) -> None:
        self.ok = ok
        self.text = text

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ValidationResult(ok={self.ok}, text={self.text!r})"


class ConfigViewModel:
    """Backs the project/config/flow/limits panels."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.session = Session(path) if path else None
        self.error = ""

    # ------------------------------------------------------------------ open
    @property
    def opened(self) -> bool:
        """True when a session file was opened successfully."""
        return self.session is not None and not self.error

    def open(self, path: str | Path) -> ValidationResult:
        """Open a session file and report whether it is usable."""
        self.session = Session(path)
        self.error = ""
        try:
            self.session.load(force=True)
        except ConfigProblem as exc:
            self.error = str(exc)
            return ValidationResult(False, self.error)
        return self.validate()

    def validate(self) -> ValidationResult:
        """Re-validate the configuration."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        report = self.session.validate()
        return ValidationResult(report.ok, report.format())

    # ------------------------------------------------------------------- tree
    def tree(self) -> list[dict[str, Any]]:
        """Nested description of the configuration for a tree view."""
        if self.session is None:
            return []
        try:
            info = self.session.info()
        except ConfigProblem as exc:
            return [{"label": f"<error: {exc}>", "children": []}]
        loaded = self.session.loaded
        return [
            {"label": f"session: {info.name}", "children": [
                {"label": f"path: {info.path}", "children": []},
                {"label": f"stage: {info.stage}", "children": []},
                {"label": f"sites: {info.sites}", "children": []},
                {"label": f"temperature: {info.temperature} C", "children": []},
            ]},
            {"label": f"program: {info.program}", "children": [
                {"label": f"stage {stage}",
                 "children": [{"label": f"{item['test_num']} {item['item']}",
                               "children": []}
                              for item in self.session.items(stage)]}
                for stage in info.stages
            ]},
            {"label": f"dut: {info.dut_model}", "children": [
                {"label": f"yield: {loaded.dut_profile.yield_.pass_rate}",
                 "children": []},
                {"label": f"defects: {len(loaded.dut_profile.defects)}", "children": []},
            ]},
            {"label": "levels", "children": [
                {"label": f"{item.name}: {item.vdd.nom} V", "children": []}
                for item in loaded.levels.sets
            ]},
            {"label": "timing", "children": [
                {"label": f"{item.name}: {item.period_ns} ns", "children": []}
                for item in loaded.timing.sets
            ]},
            {"label": "binning", "children": [
                {"label": f"pass bin {loaded.binning.pass_bin}", "children": []},
                {"label": f"soft bins: {len(loaded.binning.soft_bins)}", "children": []},
                {"label": f"hard bins: {len(loaded.binning.hard_bins)}", "children": []},
            ]},
            {"label": "files", "children": [
                {"label": f"{key}: {value}", "file_key": key, "children": []}
                for key, value in sorted(loaded.files.items())
            ] + [
                {"label": f"session: {loaded.path}", "file_key": "session", "children": []},
                {"label": f"run: {loaded.path}", "file_key": "run", "children": []},
            ]},
        ]

    def flow_tree(self, stage: str | None = None) -> list[dict[str, Any]]:
        """Flow tree of one stage."""
        if self.session is None:
            return []
        return self.session.flow(stage)

    def stages(self) -> list[str]:
        """Available stages."""
        if self.session is None:
            return []
        try:
            return self.session.stages()
        except ConfigProblem:
            return []

    # --------------------------------------------------------------- overrides
    def set_override(self, key: str, value: Any) -> ValidationResult:
        """Set an override and re-validate."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        self.session.set(key, value)
        return self.validate()

    def overrides(self) -> dict[str, Any]:
        """Current overrides (shown in the GUI)."""
        return dict(self.session.overrides) if self.session is not None else {}

    # ----------------------------------------------------------------- limits
    def limits_rows(self) -> list[dict[str, Any]]:
        """Editable limits rows."""
        if self.session is None:
            return []
        return self.session.limits()

    def update_limit(self, test_num: int, *, lo: float | None = None,
                     hi: float | None = None) -> ValidationResult:
        """Change one limit and re-validate the session."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        if not self.session.update_limit(test_num, lo=lo, hi=hi):
            return ValidationResult(False, f"test {test_num} has no limit row")
        report = self.session.validate()
        return ValidationResult(report.ok, report.format())

    def save_limits(self, path: str | Path | None = None) -> Path:
        """Write the (edited) limits back to CSV."""
        if self.session is None:
            raise RuntimeError("no session loaded")
        loaded = self.session.loaded
        target = Path(path) if path else Path(loaded.files.get("limits", "limits.csv"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["test_num", "name", "lo", "hi", "unit", "scale",
                             "stage", "temp_min", "temp_max"])
            for limit in loaded.limits.limits:
                writer.writerow([
                    limit.test_num, limit.name,
                    "" if limit.lo is None else limit.lo,
                    "" if limit.hi is None else limit.hi,
                    limit.unit, limit.scale, limit.stage, limit.temp_min, limit.temp_max,
                ])
        return target

    # ------------------------------------------------------------------- misc
    def bins(self) -> dict[str, Any]:
        """Soft/hard bin tables."""
        return self.session.bins() if self.session is not None else {}

    def snapshot(self) -> dict[str, Any]:
        """Resolved configuration."""
        return self.session.snapshot() if self.session is not None else {}

    # --------------------------------------------------------------- documents
    def file_document(self, key: str) -> Any:
        """One file for the editor, or None when nothing is loaded."""
        if self.session is None:
            return None
        return self.session.file_document(key)

    def save_text(self, key: str, text: str) -> ValidationResult:
        """Parse and save one file. A failed validation leaves the file untouched."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        result = self.session.save_file_text(key, text)
        return ValidationResult(result.ok, result.text)

    def save_data(self, key: str, data: Any) -> ValidationResult:
        """Validate ``data`` and write the file it belongs to."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        result = self.session.save_file_data(key, data)
        return ValidationResult(result.ok, result.text)

    def save_program(self) -> ValidationResult:
        """Write the in-memory flow back to testprogram.yaml."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        result = self.session.save_program()
        return ValidationResult(result.ok, result.text)

    def save_binning(self, data: dict[str, Any]) -> ValidationResult:
        """Write binning.yaml."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        result = self.session.save_binning(data)
        return ValidationResult(result.ok, result.text)

    def set_enabled(self, stage: str, path: list[int], enabled: bool) -> None:
        """Toggle a flow node and leave saving to the caller."""
        if self.session is not None:
            self.session.set_enabled(stage, path, enabled)

    def set_item_params(self, stage: str, path: list[int], params: dict[str, Any]) -> ValidationResult:
        """Update one item's parameters."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        try:
            self.session.set_item_params(stage, path, params)
        except Exception as exc:  # noqa: BLE001 - pydantic or a missing node
            return ValidationResult(False, str(exc))
        return ValidationResult(True, "parameters updated")

    def set_on_fail(self, stage: str, path: list[int], *, action: str,
                    bin: int | None, max_retries: int) -> ValidationResult:
        """Update one node's on-fail policy."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        try:
            self.session.set_on_fail(stage, path, action=action, bin=bin,
                                     max_retries=max_retries)
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(False, str(exc))
        return ValidationResult(True, "on_fail updated")

    def move_flow(self, stage: str, path: list[int], delta: int) -> bool:
        """Move a flow node among its siblings."""
        if self.session is None:
            return False
        return self.session.move_flow(stage, path, delta)

    def reorder_flow(self, stage: str, parent_path: list[int], order: list[int]) -> ValidationResult:
        """Apply a sibling permutation."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        try:
            self.session.reorder_flow(stage, parent_path, order)
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(False, str(exc))
        return ValidationResult(True, "flow reordered")

    def item_schema(self, name: str) -> dict[str, Any]:
        """Parameter schema for a test item, or an empty object when unknown."""
        if self.session is None or not name:
            return {}
        try:
            return self.session.item_schema(name)
        except Exception:  # noqa: BLE001 - unknown item, the form stays empty
            return {}

    def import_limits_csv(self, text: str) -> ValidationResult:
        """Replace limits from CSV text and write the session limits file."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        result = self.session.save_file_text("limits", text)
        return ValidationResult(result.ok, result.text)

    def export_limits_csv(self, path: str) -> ValidationResult:
        """Copy the current limits to ``path``."""
        if self.session is None:
            return ValidationResult(False, "no session loaded")
        try:
            target = self.session.export_limits_csv(path)
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(False, str(exc))
        return ValidationResult(True, f"exported {target}")
