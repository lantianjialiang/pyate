"""Gate coverage.py JSON on core line and branch coverage, excluding GUI/API."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CORE = re.compile(r"(?:^|/)pyate/(?:config|hal|dut|pattern|engine|datalog)/")


def core_coverage(data: dict) -> tuple[float, int]:
    covered = total = files = 0
    for name, result in data['files'].items():
        if not CORE.search(name.replace('\\', '/')):
            continue
        summary = result['summary']
        covered += summary['covered_lines'] + summary['covered_branches']
        total += summary['num_statements'] + summary['num_branches']
        files += 1
    if not files or not total:
        raise ValueError('no core line/branch coverage found')
    return 100.0 * covered / total, files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--fail-under', type=float, default=85)
    args = parser.parse_args()
    if not 0 <= args.fail_under <= 100:
        parser.error('fail-under must be between 0 and 100')
    try:
        percent, files = core_coverage(json.loads(args.report.read_text(encoding='utf-8')))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Invalid coverage report: {exc}\n')
    print(f'Core coverage: {percent:.2f}% across {files} files; required {args.fail_under:.2f}%')
    raise SystemExit(0 if percent >= args.fail_under else 1)


if __name__ == '__main__':
    main()
