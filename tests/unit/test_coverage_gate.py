"""The quality gate must count core branches and reject empty evidence."""
import runpy
from pathlib import Path

import pytest

gate = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tools/check_core_coverage.py'))['core_coverage']


def record(lines=10, covered=8, branches=10, covered_branches=7):
    return {'summary': {'num_statements': lines, 'covered_lines': covered,
                        'num_branches': branches, 'covered_branches': covered_branches}}


def test_gate_includes_branches_and_excludes_gui():
    value, files = gate({'files': {'src/pyate/engine/site.py': record(),
                                  'src/pyate/gui/panels.py': record(1000, 1000, 1000, 1000)}})
    assert value == 75 and files == 1


def test_gate_accepts_windows_absolute_paths():
    value, files = gate({'files': {r'D:\repo\src\pyate\hal\base.py': record(10, 10, 0, 0)}})
    assert value == 100 and files == 1


def test_gate_rejects_reports_without_core_evidence():
    with pytest.raises(ValueError, match='no core'):
        gate({'files': {'src/pyate/gui/panels.py': record()}})
