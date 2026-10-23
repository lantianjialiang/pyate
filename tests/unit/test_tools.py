"""Tests for the helper scripts in ``tools/``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import ROOT

TOOLS = ROOT / "tools"


def _load(name: str):  # noqa: ANN202 - dynamic module
    spec = importlib.util.spec_from_file_location(f"pyate_tools_{name}", TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():  # noqa: ANN201
    return _load("export_wafer_map")


@pytest.fixture(scope="module")
def coverage_tool():  # noqa: ANN201
    return _load("coverage_std")


def test_binmap_reader_keeps_every_die(exporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "binmap.csv"
    csv_path.write_text("x,y,bin,part_id\n0,0,1,W-X0Y0\n1,0,2,W-X1Y0\n-1,0,0,W-X-1Y0\n",
                        encoding="utf-8")
    dies = exporter.read_binmap(csv_path)
    assert dies == {(0, 0): 1, (1, 0): 2, (-1, 0): 0}


def test_binmap_reader_ignores_broken_rows(exporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "binmap.csv"
    csv_path.write_text("x,y,bin\n0,0,1\nnot,a,row\n1,1,\n", encoding="utf-8")
    dies = exporter.read_binmap(csv_path)
    assert (0, 0) in dies
    assert all(isinstance(key, tuple) and len(key) == 2 for key in dies)


def test_binmap_reader_rejects_an_empty_file(exporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        exporter.read_binmap(csv_path)


def test_export_writes_a_png(exporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "W01_binmap.csv"
    csv_path.write_text("x,y,bin\n0,0,1\n1,0,1\n0,1,2\n", encoding="utf-8")
    target = tmp_path / "map.png"
    assert exporter.main([str(csv_path), "--out", str(target), "--size", "200"]) == 0
    assert target.read_bytes().startswith(b"\x89PNG")


def test_export_without_dies_is_an_error(exporter, tmp_path: Path) -> None:
    csv_path = tmp_path / "W01_binmap.csv"
    csv_path.write_text("x,y,bin\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        exporter.main([str(csv_path), "--out", str(tmp_path / "none.png")])


def test_coverage_tool_lists_source_files(coverage_tool) -> None:
    files = coverage_tool._source_files(ROOT / "src" / "pyate")
    assert any(path.name == "site.py" for path in files)
    assert all("__pycache__" not in path.parts for path in files)


def test_coverage_tool_reads_executable_lines(coverage_tool) -> None:
    lines = coverage_tool._executable_lines(ROOT / "src" / "pyate" / "engine" / "site.py")
    assert len(lines) > 50
    assert all(isinstance(line, int) and line > 0 for line in lines)


def test_coverage_tool_reuses_saved_counts(coverage_tool, tmp_path: Path) -> None:
    import json

    target = ROOT / "src" / "pyate" / "engine" / "site.py"
    counts = tmp_path / "counts.json"
    counts.write_text(json.dumps({f"{target}:10": 3}), encoding="utf-8")
    code = coverage_tool.main(["--counts", str(counts), "--fail-under", "0", "--json"])
    assert code == 0
