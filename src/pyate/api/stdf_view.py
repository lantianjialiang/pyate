"""Read an STDF file into the numbers the GUI summary can draw."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..datalog.stdf.reader_check import read_stdf

__all__ = ["inspect_stdf"]

_FAIL = 0x02
_NOT_EXECUTED = 0x08
_PART_NOT_TESTED = 0x01
_PART_FAIL = 0x02


def _open_limit(value: Any) -> float | None:
    """PTR limit, or ``None`` when the field is missing or the ±1e38 sentinel."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) >= 1e30:
        return None
    return number


def inspect_stdf(path: str | Path) -> dict[str, Any]:
    """Decode one STDF file.

    ``stats`` is the file header (lot, wafers, bins, record counts).
    ``measurements`` is one dict per PTR/FTR so the summary table can group
    them by test, site or wafer.
    """
    file_path = Path(path)
    empty: dict[str, Any] = {
        "path": str(file_path),
        "ok": False,
        "errors": [],
        "stats": _empty_stats(),
        "measurements": [],
    }
    if not file_path.is_file():
        empty["errors"] = ["file not found"]
        return empty
    try:
        records = read_stdf(file_path)
    except Exception as exc:  # noqa: BLE001 - shown in the STDF panel
        empty["errors"] = [f"cannot decode: {exc}"]
        return empty
    return _assemble(file_path, records)


def _empty_stats() -> dict[str, Any]:
    return {
        "lot_id": "",
        "part_typ": "",
        "job_nam": "",
        "node_nam": "",
        "test_cod": "",
        "tst_temp": "",
        "parts": 0,
        "good": 0,
        "retests": 0,
        "yield_pct": 0.0,
        "wafers": [],
        "hard_bins": [],
        "soft_bins": [],
        "record_counts": {},
        "ptr_count": 0,
        "ftr_count": 0,
    }


def _assemble(path: Path, records: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    stats = _empty_stats()
    counts: dict[str, int] = {}
    current_wafer = ""
    site_wafer: dict[int, str] = {}
    open_parts: dict[int, list[int]] = {}
    wafer_parts: dict[str, dict[str, int]] = {}
    wafer_wrr: dict[str, dict[str, int]] = {}
    hard: dict[int, dict[str, Any]] = {}
    soft: dict[int, dict[str, Any]] = {}
    measurements: list[dict[str, Any]] = []
    for name, values in records:
        counts[name] = counts.get(name, 0) + 1
        if name == "MIR":
            stats["lot_id"] = str(values.get("LOT_ID") or "")
            stats["part_typ"] = str(values.get("PART_TYP") or "")
            stats["job_nam"] = str(values.get("JOB_NAM") or "")
            stats["node_nam"] = str(values.get("NODE_NAM") or "")
            stats["test_cod"] = str(values.get("TEST_COD") or "")
            stats["tst_temp"] = str(values.get("TST_TEMP") or "")
        elif name == "WIR":
            current_wafer = str(values.get("WAFER_ID") or "")
        elif name == "PIR":
            site = int(values.get("SITE_NUM") or 0)
            site_wafer[site] = current_wafer
            open_parts[site] = []
        elif name in ("PTR", "FTR"):
            flags = int(values.get("TEST_FLG") or 0)
            if flags & _NOT_EXECUTED:
                continue
            site = int(values.get("SITE_NUM") or 0)
            if name == "PTR":
                stats["ptr_count"] += 1
                value = values.get("RESULT")
            else:
                stats["ftr_count"] += 1
                value = None
            measurements.append({
                "test_num": int(values.get("TEST_NUM") or 0),
                "name": str(values.get("TEST_TXT") or ""),
                "site": site,
                "wafer": site_wafer.get(site, current_wafer),
                "value": None if value is None else float(value),
                "passed": not bool(flags & _FAIL),
                "part": "",
                "lo": _open_limit(values.get("LO_LIMIT")) if name == "PTR" else None,
                "hi": _open_limit(values.get("HI_LIMIT")) if name == "PTR" else None,
            })
            open_parts.setdefault(site, []).append(len(measurements) - 1)
        elif name == "PRR":
            site = int(values.get("SITE_NUM") or 0)
            part_id = str(values.get("PART_ID") or "")
            if not part_id and values.get("X_COORD") is not None and values.get("Y_COORD") is not None:
                part_id = f"{values.get('X_COORD')},{values.get('Y_COORD')}"
            for index in open_parts.pop(site, []):
                measurements[index]["part"] = part_id
            wafer = site_wafer.pop(site, current_wafer)
            flags = int(values.get("PART_FLG") or 0)
            bucket = wafer_parts.setdefault(wafer, {"parts": 0, "good": 0})
            bucket["parts"] += 1
            stats["parts"] += 1
            if flags & 0x04:
                stats["retests"] += 1
            passed = not (flags & (_PART_NOT_TESTED | _PART_FAIL))
            if passed:
                stats["good"] += 1
                bucket["good"] += 1
            hbin = int(values.get("HARD_BIN") or 0)
            sbin = int(values.get("SOFT_BIN") or 0)
            hard.setdefault(hbin, {"bin": hbin, "count": 0, "name": "", "pf": ""})
            soft.setdefault(sbin, {"bin": sbin, "count": 0, "name": "", "pf": ""})
            hard[hbin]["count"] += 1
            soft[sbin]["count"] += 1
        elif name == "WRR":
            wafer = str(values.get("WAFER_ID") or current_wafer)
            wafer_wrr[wafer] = {
                "parts": int(values.get("PART_CNT") or 0),
                "good": int(values.get("GOOD_CNT") or 0),
            }
        elif name == "HBR":
            number = int(values.get("HBIN_NUM") or 0)
            row = hard.setdefault(number, {"bin": number, "count": 0, "name": "", "pf": ""})
            row["count"] = int(values.get("HBIN_CNT") or row["count"])
            row["name"] = str(values.get("HBIN_NAM") or row["name"])
            row["pf"] = str(values.get("HBIN_PF") or row["pf"])
        elif name == "SBR":
            number = int(values.get("SBIN_NUM") or 0)
            row = soft.setdefault(number, {"bin": number, "count": 0, "name": "", "pf": ""})
            row["count"] = int(values.get("SBIN_CNT") or row["count"])
            row["name"] = str(values.get("SBIN_NAM") or row["name"])
            row["pf"] = str(values.get("SBIN_PF") or row["pf"])
    source = wafer_wrr or wafer_parts
    stats["wafers"] = [
        {"wafer_id": wafer, "parts": data["parts"], "good": data["good"]}
        for wafer, data in sorted(source.items())
    ]
    stats["hard_bins"] = [hard[key] for key in sorted(hard)]
    stats["soft_bins"] = [soft[key] for key in sorted(soft)]
    stats["record_counts"] = dict(sorted(counts.items()))
    stats["yield_pct"] = (100.0 * stats["good"] / stats["parts"]) if stats["parts"] else 0.0
    return {
        "path": str(path),
        "ok": True,
        "errors": [],
        "stats": stats,
        "measurements": measurements,
    }
