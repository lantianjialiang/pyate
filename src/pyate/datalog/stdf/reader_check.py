"""STDF verification: structural checks plus an optional pystdf cross-check."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .records import decode_record

__all__ = ["VerifyReport", "read_stdf", "verify_stdf", "pystdf_summary"]


@dataclass
class VerifyReport:
    """Result of verifying one STDF file."""

    path: str
    records: int = 0
    parts: int = 0
    ptr_count: int = 0
    ftr_count: int = 0
    hard_bins: dict[int, int] = field(default_factory=dict)
    soft_bins: dict[int, int] = field(default_factory=dict)
    pcr_totals: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    record_counts: dict[str, int] = field(default_factory=dict)
    retest_parts: int = 0
    pystdf_ok: bool | None = None
    pystdf_error: str | None = None

    @property
    def ok(self) -> bool:
        """True when no structural error and pystdf (if used) accepted the file."""
        return not self.errors and self.pystdf_ok is not False

    def format(self) -> str:
        """Human readable report."""
        lines = [
            f"{self.path}",
            f"  records: {self.records}  parts: {self.parts}  "
            f"PTR: {self.ptr_count}  FTR: {self.ftr_count}",
            f"  hard bins: {dict(sorted(self.hard_bins.items()))}",
            f"  soft bins: {dict(sorted(self.soft_bins.items()))}",
            f"  record types: {dict(sorted(self.record_counts.items()))}",
        ]
        if self.pystdf_ok is not None:
            lines.append(f"  pystdf: {'OK' if self.pystdf_ok else 'FAILED - ' + str(self.pystdf_error)}")
        lines += [f"  error: {msg}" for msg in self.errors]
        lines += [f"  warn:  {msg}" for msg in self.warnings]
        lines.append(f"  => {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)


def _open_binary(path: Path) -> Any:
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def read_stdf(path: str | Path) -> list[tuple[str, dict[str, Any]]]:
    """Read every record of a (optionally gzipped) STDF file."""
    file_path = Path(path)
    data = _open_binary(file_path).read()
    records: list[tuple[str, dict[str, Any]]] = []
    offset = 0
    while offset + 4 <= len(data):
        values, offset, name = decode_record(data, offset)
        records.append((name, values))
    if offset != len(data):
        raise ValueError(f"{file_path}: trailing {len(data) - offset} byte(s) after last record")
    return records


def verify_stdf(path: str | Path, *, use_pystdf: bool = True) -> VerifyReport:
    """Validate record order, PIR/PRR pairing and summary consistency."""
    file_path = Path(path)
    report = VerifyReport(path=str(file_path))
    if not file_path.is_file():
        report.errors.append("file not found")
        return report
    try:
        records = read_stdf(file_path)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"cannot decode: {exc}")
        return report

    report.records = len(records)
    if not records:
        report.errors.append("empty file")
        return report
    if records[0][0] != "FAR":
        report.errors.append(f"first record is {records[0][0]}, expected FAR")
    else:
        far = records[0][1]
        if far.get("STDF_VER") != 4:
            report.errors.append(f"STDF version {far.get('STDF_VER')} is not 4")
        if far.get("CPU_TYPE") not in (1, 2):
            report.errors.append(f"invalid CPU_TYPE {far.get('CPU_TYPE')}")

    open_parts: dict[int, int] = {}
    seen_mir = False
    seen_prr = False
    for index, (name, values) in enumerate(records):
        report.record_counts[name] = report.record_counts.get(name, 0) + 1
        if name == "MIR":
            seen_mir = True
        elif name == "PIR":
            if not seen_mir:
                report.errors.append(f"PIR at record {index} before MIR")
            site = int(values.get("SITE_NUM") or 0)
            if site in open_parts:
                report.errors.append(f"PIR for site {site} while previous part still open")
            open_parts[site] = 0
        elif name in ("PTR", "FTR"):
            site = int(values.get("SITE_NUM") or 0)
            if site not in open_parts:
                report.errors.append(f"{name} at record {index} outside a part")
            else:
                open_parts[site] += 1
            if name == "PTR":
                report.ptr_count += 1
            else:
                report.ftr_count += 1
        elif name == "PRR":
            seen_prr = True
            site = int(values.get("SITE_NUM") or 0)
            written = open_parts.pop(site, None)
            if written is None:
                report.errors.append(f"PRR for site {site} without PIR")
            else:
                num_test = int(values.get("NUM_TEST") or 0)
                if num_test != written:
                    report.errors.append(
                        f"PRR site {site}: NUM_TEST={num_test} but {written} test record(s) written"
                    )
            report.parts += 1
            if int(values.get("PART_FLG") or 0) & 0x04:
                report.retest_parts += 1
            hbin = int(values.get("HARD_BIN") or 0)
            sbin = int(values.get("SOFT_BIN") or 0)
            report.hard_bins[hbin] = report.hard_bins.get(hbin, 0) + 1
            report.soft_bins[sbin] = report.soft_bins.get(sbin, 0) + 1
        elif name == "PCR":
            head = int(values.get("HEAD_NUM") or 0)
            site = int(values.get("SITE_NUM") or 0)
            count = int(values.get("PART_CNT") or 0)
            if head == 255 and site == 255:
                report.pcr_totals[site] = count
    if open_parts:
        report.errors.append(f"unclosed part(s) for site(s) {sorted(open_parts)}")
    if not seen_prr:
        report.errors.append("no PRR record found")
    total = report.pcr_totals.get(255)
    if total is not None and total != report.parts:
        report.errors.append(f"PCR total {total} differs from PRR count {report.parts}")
    hbr_counts: dict[int, int] = {}
    sbr_counts: dict[int, int] = {}
    for name, values in records:
        if name == "HBR":
            hbr_counts[int(values.get("HBIN_NUM") or 0)] = int(values.get("HBIN_CNT") or 0)
        elif name == "SBR":
            sbr_counts[int(values.get("SBIN_NUM") or 0)] = int(values.get("SBIN_CNT") or 0)
    if hbr_counts and sum(hbr_counts.values()) != report.parts:
        report.errors.append(
            f"HBR counts sum {sum(hbr_counts.values())} differ from parts {report.parts}"
        )
    if sbr_counts and sum(sbr_counts.values()) != report.parts:
        report.errors.append(
            f"SBR counts sum {sum(sbr_counts.values())} differ from parts {report.parts}"
        )
    if "MRR" not in report.record_counts:
        report.warnings.append("no MRR record (file may be truncated)")

    if use_pystdf:
        summary = pystdf_summary(file_path)
        report.pystdf_ok = summary.get("ok")
        report.pystdf_error = summary.get("error")
        if not summary.get("ok"):
            report.errors.append(f"pystdf rejected the file: {summary.get('error')}")
        elif summary.get("parts") not in (None, report.parts):
            report.warnings.append(
                f"pystdf saw {summary.get('parts')} parts, reader saw {report.parts}"
            )
    return report


def pystdf_summary(path: str | Path) -> dict[str, Any]:
    """Parse a file with ``pystdf`` and summarise it (``ok=False`` on error)."""
    try:
        from pystdf.IO import Parser
    except Exception as exc:  # pragma: no cover - optional dependency
        return {"ok": None, "error": f"pystdf not available: {exc}"}

    class _Sink:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        def after_send(self, _source: Any, data: Any) -> None:
            """Collect per-record-type counts (pystdf passes source then record)."""
            record_type, _fields = data
            name = getattr(record_type, "__name__", str(record_type))
            self.counts[name] = self.counts.get(name, 0) + 1

    sink = _Sink()
    try:
        with _open_binary(Path(path)) as handle:
            parser = Parser(inp=handle)
            parser.addSink(sink)
            parser.parse()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "counts": sink.counts, "parts": sink.counts.get("Prr")}
