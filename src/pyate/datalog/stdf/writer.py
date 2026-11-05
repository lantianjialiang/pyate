"""Streaming STDF V4 writer and datalog sink."""

from __future__ import annotations

import gzip
import math
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from ..._files import replace_atomic
from ...engine.context import PartResult, TestResult
from .records import (
    ATR,
    FAR,
    FTR,
    HBR,
    MIR,
    MRR,
    PCR,
    PIR,
    PMR,
    PRR,
    PTR,
    SBR,
    SDR,
    TSR,
    WCR,
    WIR,
    WRR,
    RecordSpec,
    encode_record,
)

__all__ = ["PartFlags", "StdfSink", "StdfWriter", "TestFlags"]

HEAD_ALL = 1
SITE_ALL = 255


class PartFlags:
    """PART_FLG bit values (STDF V4 section 5.4.6).

    A passing part carries the value ``0``; the individual failure / retest /
    abnormal conditions are additive bits on top of it.
    """

    PASS = 0x00
    TESTED = 0x00
    NOT_TESTED = 0x10
    FAIL = 0x08
    RETEST = 0x01  # supersede the preceding sequence with the same PART_ID
    RETEST_XY = 0x02
    ABNORMAL = 0x04


class TestFlags:
    """TEST_FLG bit values."""

    PASS = 0x00
    ALARM = 0x01
    FAIL = 0x80
    INVALID = 0x02
    TIMEOUT = 0x08
    NOT_EXECUTED = 0x10
    INCOMPLETE = 0x20


@dataclass
class _SiteStats:
    parts: int = 0
    retests: int = 0
    good: int = 0
    aborts: int = 0


@dataclass
class _TestStats:
    exec_count: int = 0
    fail_count: int = 0
    total_ms: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    sums: float = 0.0
    squares: float = 0.0


class StdfWriter:
    """Writes one STDF V4 file (optionally gzip compressed)."""

    def __init__(
        self,
        path: str | Path,
        *,
        byte_order: str = "little",
        cpu_type: int | None = None,
        gzip_output: bool = False,
        write_wcr: bool = False,
        write_fail_pin: bool = False,
        site_count: int = 1,
        tester_name: str = "PYATE",
        tester_model: str = "PYATE-SIM",
        hand_type: str = "PROBER",
        card_id: str = "CARD1",
        load_id: str = "LOAD1",
        pin_maps: dict[int, dict[str, int]] | None = None,
    ) -> None:
        expected_cpu = 2 if byte_order == "little" else 1
        if byte_order not in ("little", "big") or cpu_type not in (None, expected_cpu):
            raise ValueError("CPU_TYPE must match the STDF byte order")
        self.path = Path(str(path) + (".gz" if gzip_output and not str(path).endswith(".gz") else ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_path = self.path.with_name(self.path.name + ".partial")
        raw: BinaryIO = open(self._pending_path, "wb")  # noqa: SIM115 - owned until close(), across records
        self._handle: BinaryIO | gzip.GzipFile = gzip.GzipFile(fileobj=raw, mode="wb") if gzip_output else raw
        self._raw = raw
        self.endian = "little" if byte_order == "little" else "big"
        self.cpu_type = expected_cpu
        self.wcr_enabled = bool(write_wcr)
        self.write_fail_pin = write_fail_pin
        self.site_count = site_count
        self.tester_name = tester_name
        self.tester_model = tester_model
        self.hand_type = hand_type
        self.card_id = card_id
        self.load_id = load_id
        self.pin_maps = pin_maps or {}
        self.pin_indices = {(site, pin): index for index, (site, pin) in enumerate(
            [(site, pin) for site in sorted(self.pin_maps) for pin in sorted(self.pin_maps[site])], 1
        )}
        self.start_t = int(time.time()) & 0xFFFFFFFF
        self._sites = {site: _SiteStats() for site in range(1, site_count + 1)}
        self._tests: dict[tuple[int, int, str, str], _TestStats] = {}
        self._hard_bins: dict[int, int] = {}
        self._soft_bins: dict[int, int] = {}
        self._closed = False
        self.part_count = 0
        self.retest_count = 0
        self._current_part: PartResult | None = None
        self._current_results: list[TestResult] = []
        self._start_wir: int | None = None
        self._write_far()

    # ---------------------------------------------------------------- lowlevel
    def _write(self, spec: RecordSpec, values: dict[str, Any], *, fields: int | None = None) -> None:
        if fields is not None:
            spec = RecordSpec(spec.name, spec.typ, spec.sub, spec.fields[:fields])
        self._handle.write(encode_record(spec, values, endian=self.endian))

    def _write_far(self) -> None:
        self._write(FAR, {"CPU_TYPE": self.cpu_type, "STDF_VER": 4})

    # --------------------------------------------------------------- metadata
    def write_atr(self, command_line: str) -> None:
        """Optional audit trail record."""
        self._write(ATR, {"MOD_TIM": int(time.time()) & 0xFFFFFFFF, "CMD_LINE": command_line})

    def write_mir(self, *, lot_id: str, sublot: str = "", part_type: str = "DUT",
                  job_name: str = "PROGRAM", stage: str = "CP1", operator: str = "PYATE",
                  temperature: float = 25.0, flow_id: str | None = None,
                  setup_id: str = "", node_name: str | None = None,
                  serial_number: str = "") -> None:
        """Write the master information record.

        ``serial_number`` identifies the *tester*, so it must be stable across
        runs of the same configuration (otherwise the run is not reproducible).
        """
        self._write(MIR, {
            "SETUP_T": self.start_t, "START_T": self.start_t, "STAT_NUM": 1,
            "MODE_COD": "P", "RTST_COD": " ", "PROT_COD": " ", "BURN_TIM": 0,
            "CMOD_COD": " ", "LOT_ID": lot_id, "PART_TYP": part_type,
            "NODE_NAM": node_name or self.tester_name, "TSTR_TYP": self.tester_model,
            "JOB_NAM": job_name, "JOB_REV": "1.0", "SBLOT_ID": sublot, "OPER_NAM": operator,
            "EXEC_TYP": "PYATE", "EXEC_VER": "1.0", "TEST_COD": stage,
            "TST_TEMP": f"{temperature:.1f}", "USER_TXT": "", "AUX_FILE": "",
            "PKG_TYP": "", "FAMLY_ID": "", "DATE_COD": time.strftime("%Y%m%d"),
            "FACIL_ID": "", "FLOOR_ID": "", "PROC_ID": "", "OPER_FRQ": "",
            "SPEC_NAM": "", "SPEC_VER": "", "FLOW_ID": flow_id or stage,
            "SETUP_ID": setup_id, "DSGN_REV": "", "ENG_ID": "", "ROM_COD": "",
            "SERL_NUM": serial_number or self.tester_model, "SUPR_NAM": "",
        })

    def write_sdr(self, site_count: int | None = None, *, hand_type: str | None = None,
                  load_id: str | None = None, card_id: str | None = None) -> None:
        """Write the site description record."""
        count = int(site_count or self.site_count)
        self._write(SDR, {
            "HEAD_NUM": HEAD_ALL, "SITE_GRP": 0, "SITE_CNT": count,
            "SITE_NUM": list(range(1, count + 1)),
            "HAND_TYP": hand_type or self.hand_type, "HAND_ID": "H1",
            "CARD_TYP": "PROBE_CARD" if (hand_type or self.hand_type) == "PROBER" else "DIB",
            "CARD_ID": card_id or self.card_id, "LOAD_TYP": "BOARD",
            "LOAD_ID": load_id or self.load_id, "DIB_TYP": "", "DIB_ID": "",
            "CABL_TYP": "", "CABL_ID": "", "CONT_TYP": "", "CONT_ID": "",
            "LASR_TYP": "", "LASR_ID": "", "EXTR_TYP": "", "EXTR_ID": "",
        })
        for (site, pin), index in self.pin_indices.items():
            self._write(PMR, {"PMR_INDX": index, "CHAN_TYP": 0,
                              "CHAN_NAM": str(self.pin_maps[site][pin]), "PHY_NAM": pin,
                              "LOG_NAM": pin, "HEAD_NUM": HEAD_ALL, "SITE_NUM": site})

    def write_wir(self, wafer_id: str) -> None:
        """Start a wafer (CP)."""
        self._start_wir = int(time.time()) & 0xFFFFFFFF
        self._write(WIR, {"HEAD_NUM": HEAD_ALL, "SITE_GRP": 0,
                          "START_T": self._start_wir, "WAFER_ID": wafer_id})

    def write_wcr_record(
        self,
        *,
        wafer_size_mm: float | None = None,
        die_w_mm: float | None = None,
        die_h_mm: float | None = None,
        center_x: int = 0,
        center_y: int = 0,
        wafer_units: int = 3,
        notch: str = "bottom",
    ) -> None:
        """Write the optional WCR wafer configuration record.

        WCR is only emitted when the writer was created with ``write_wcr=True``.
        Geometry falls back to the values supplied when the record is written, so
        a caller can pass the real wafer/die dimensions from the session config.
        """
        if not self.wcr_enabled:
            return
        self._write(WCR, {
            "WAFR_SIZ": float(wafer_size_mm or 300.0),
            "DIE_HT": float(die_h_mm or 10.0),
            "DIE_WID": float(die_w_mm or 10.0),
            "WF_UNITS": int(wafer_units),
            "WF_FLAT": {"bottom": "D", "top": "U", "left": "L", "right": "R"}[notch],
            "CENTER_X": int(center_x), "CENTER_Y": int(center_y),
            "POS_X": "R", "POS_Y": "U",
        })

    # ------------------------------------------------------------------- part
    def start_part(self, part: PartResult) -> None:
        """Write PIR for a part."""
        self._current_part = part
        self._current_results = []
        self._write(PIR, {"HEAD_NUM": HEAD_ALL, "SITE_NUM": part.site})

    def write_result(self, result: TestResult) -> None:
        """Write one PTR or FTR record."""
        if result.skipped:
            return
        self._current_results.append(result)
        stats = self._tests.setdefault(
            (result.site, result.test_num, result.kind, result.test_name), _TestStats()
        )
        stats.exec_count += 1
        if not result.passed:
            stats.fail_count += 1
        stats.total_ms += result.test_time_ms
        if result.kind == "PTR" and result.value is not None and math.isfinite(result.value):
            value = float(result.value)
            stats.minimum = value if stats.minimum is None else min(stats.minimum, value)
            stats.maximum = value if stats.maximum is None else max(stats.maximum, value)
            stats.sums += value
            stats.squares += value * value
        if result.kind == "FTR":
            self._write_ftr(result)
        else:
            self._write_ptr(result)

    def _test_flags(self, result: TestResult) -> int:
        if result.skipped:
            return TestFlags.NOT_EXECUTED
        flags = TestFlags.PASS if result.passed else TestFlags.FAIL
        if result.kind != "FTR" and (result.value is None or not result.measurement_valid):
            flags |= TestFlags.INVALID
        if result.extra.get("timeout"):
            flags |= TestFlags.TIMEOUT
        if result.execution_status != "completed":
            flags |= TestFlags.INCOMPLETE
        return flags

    def _write_ptr(self, result: TestResult) -> None:
        value = 0.0 if result.value is None else float(result.value)
        # STDF has no "no limit" flag: an absent limit is written as the
        # -1e38 / +1e38 sentinel. The record always carries every field up to
        # C_NAM so that PIN_NUM survives even for unbounded measurements.
        lo = -1.0e38 if result.lo_limit is None else float(result.lo_limit)
        hi = 1.0e38 if result.hi_limit is None else float(result.hi_limit)
        self._write(PTR, {
            "TEST_NUM": result.test_num, "HEAD_NUM": HEAD_ALL, "SITE_NUM": result.site,
            "TEST_FLG": self._test_flags(result), "PARM_FLG": 0, "RESULT": value,
            "TEST_TXT": result.test_name, "ALARM_ID": "", "OPT_FLAG": 0,
            "RES_SCAL": 0, "LLM_SCAL": 0, "HLM_SCAL": 0,
            "LO_LIMIT": lo, "HI_LIMIT": hi, "UNITS": result.unit,
            "C_RESFMT": "", "C_LLMFMT": "", "C_HLMFMT": "",
            "LO_SPEC": lo, "HI_SPEC": hi,
            "PIN_NUM": self.pin_indices.get((result.site, result.pin or ""), 0),
            "C_NAM": result.pin or "",
        })

    def _write_ftr(self, result: TestResult) -> None:
        fail_pin: Any = None
        if self.write_fail_pin and result.fail_pins:
            indices = [self.pin_indices[(result.site, pin)] for pin in result.fail_pins
                       if (result.site, pin) in self.pin_indices]
            if indices:
                fail_pin = (indices, max(self.pin_indices.values()) + 1)
        rsl_text = ""
        if not result.passed:
            first = result.extra.get("first_fail_cycle")
            rsl_text = f"FAIL@cycle {first}" if first is not None else "FAIL"
            if result.fail_pins:
                rsl_text += " pins=" + ",".join(result.fail_pins[:8])
        self._write(FTR, {
            "TEST_NUM": result.test_num, "HEAD_NUM": HEAD_ALL, "SITE_NUM": result.site,
            "TEST_FLG": self._test_flags(result), "OPT_FLAG": 0,
            "CYCL_CNT": int(result.cycle_cnt), "REL_VADR": int(result.rel_vadr),
            "REPT_CNT": 1, "NUM_FAIL": int(result.num_fail),
            "XFAIL_AD": 0, "YFAIL_AD": 0, "VECT_OFF": 0,
            "RTN_ICNT": 0, "PGM_ICNT": 0,
            "RTN_INDX": [], "RTN_STAT": [], "PGM_INDX": [], "PGM_STAT": [],
            "FAIL_PIN": fail_pin, "VECT_NAM": result.vector_name, "TIME_SET": "",
            "OP_CODE": "", "TEST_TXT": result.test_name, "ALARM_ID": "",
            "PROG_TXT": "", "RSLT_TXT": rsl_text, "PATG_NUM": 1, "SPIN_MAP": None,
        })

    def end_part(self, part: PartResult | None = None) -> None:
        """Write PRR and update counters."""
        part = part or self._current_part
        if part is None:  # pragma: no cover - defensive
            return
        flags = PartFlags.NOT_TESTED if part.skipped else (
            PartFlags.PASS if part.passed else PartFlags.FAIL
        )
        if part.retest:
            flags |= PartFlags.RETEST
        if part.extra.get("error"):
            flags |= PartFlags.ABNORMAL
        elapsed = int(max(0, round(part.test_time_ms)))
        first_fail = next((r for r in part.results if not r.passed and not r.skipped), None)
        text = ""
        if first_fail is not None:
            text = f"{first_fail.test_name} fail ({first_fail.value})"[:64]
        self._write(PRR, {
            "HEAD_NUM": HEAD_ALL, "SITE_NUM": part.site, "PART_FLG": flags,
            "NUM_TEST": len(self._current_results), "HARD_BIN": part.hard_bin,
            "SOFT_BIN": part.soft_bin, "X_COORD": part.x, "Y_COORD": part.y,
            "TEST_T": elapsed, "PART_ID": part.part_id, "PART_TXT": text,
            "PART_FIX": b"",
        })
        self.part_count += 1
        stats = self._sites.setdefault(part.site, _SiteStats())
        stats.parts += 1
        if part.retest:
            stats.retests += 1
            self.retest_count += 1
        if part.passed:
            stats.good += 1
        self._hard_bins[part.hard_bin] = self._hard_bins.get(part.hard_bin, 0) + 1
        self._soft_bins[part.soft_bin] = self._soft_bins.get(part.soft_bin, 0) + 1
        self._current_part = None
        self._current_results = []

    # -------------------------------------------------------------- summaries
    def write_summaries(self, *, hard_bin_names: dict[int, str] | None = None,
                        soft_bin_names: dict[int, str] | None = None,
                        hard_bin_pass: dict[int, str] | None = None,
                        soft_bin_pass: dict[int, str] | None = None) -> None:
        """Write TSR, HBR, SBR and PCR records."""
        for (site, test_num, kind, name), stats in sorted(self._tests.items()):
            self._write(TSR, {
                "HEAD_NUM": HEAD_ALL, "SITE_NUM": site, "TEST_TYP": "F" if kind == "FTR" else "P",
                "TEST_NUM": test_num, "EXEC_CNT": stats.exec_count,
                "FAIL_CNT": stats.fail_count, "ALRM_CNT": 0, "TEST_NAM": name,
                "SEQ_NAME": "", "TEST_LBL": "", "OPT_FLAG": 0,
                "TEST_TIM": stats.total_ms / 1000.0,
                "TEST_MIN": stats.minimum or 0.0,
                "TEST_MAX": stats.maximum or 0.0,
                "TST_SUMS": stats.sums,
                "TST_SQRS": stats.squares,
            })
        for bin_num, count in sorted(self._hard_bins.items()):
            self._write(HBR, {
                "HEAD_NUM": HEAD_ALL, "SITE_NUM": SITE_ALL, "HBIN_NUM": bin_num,
                "HBIN_CNT": count,
                "HBIN_PF": (hard_bin_pass or {}).get(bin_num, "P" if bin_num == 1 else "F"),
                "HBIN_NAM": (hard_bin_names or {}).get(bin_num, f"HBIN{bin_num}"),
            })
        for bin_num, count in sorted(self._soft_bins.items()):
            self._write(SBR, {
                "HEAD_NUM": HEAD_ALL, "SITE_NUM": SITE_ALL, "SBIN_NUM": bin_num,
                "SBIN_CNT": count,
                "SBIN_PF": (soft_bin_pass or {}).get(bin_num, "P" if bin_num == 1 else "F"),
                "SBIN_NAM": (soft_bin_names or {}).get(bin_num, f"SBIN{bin_num}"),
            })
        total_parts = sum(stats.parts for stats in self._sites.values())
        total_good = sum(stats.good for stats in self._sites.values())
        total_retest = sum(stats.retests for stats in self._sites.values())
        for site, site_stats in sorted(self._sites.items()):
            self._write(PCR, {
                "HEAD_NUM": HEAD_ALL, "SITE_NUM": site, "PART_CNT": site_stats.parts,
                "RTST_CNT": site_stats.retests, "ABRT_CNT": site_stats.aborts,
                "GOOD_CNT": site_stats.good, "FUNC_CNT": 0,
            })
        self._write(PCR, {
            "HEAD_NUM": 255, "SITE_NUM": SITE_ALL, "PART_CNT": total_parts,
            "RTST_CNT": total_retest, "ABRT_CNT": 0, "GOOD_CNT": total_good, "FUNC_CNT": 0,
        })

    def write_wrr(self, *, wafer_id: str, good: int | None = None) -> None:
        """Finish a wafer (CP)."""
        stats = list(self._sites.values())
        parts = sum(item.parts for item in stats)
        good_count = sum(item.good for item in stats) if good is None else good
        self._write(WRR, {
            "HEAD_NUM": HEAD_ALL, "SITE_GRP": 0,
            "FINISH_T": int(time.time()) & 0xFFFFFFFF, "PART_CNT": parts,
            "RTST_CNT": sum(item.retests for item in stats), "ABRT_CNT": 0,
            "GOOD_CNT": good_count, "FUNC_CNT": 0, "WAFER_ID": wafer_id,
            "FABWF_ID": "", "FRAME_ID": "", "MASK_ID": "", "USR_DESC": "", "EXC_DESC": "",
        })

    def close(self, *, disposition: str = " ", user_desc: str = "",
              exc_desc: str = "") -> Path:
        """Write MRR and close the file; returns the path."""
        if self._closed:
            return self.path
        self._write(MRR, {
            "FINISH_T": int(time.time()) & 0xFFFFFFFF, "DISP_COD": disposition,
            "USR_DESC": user_desc, "EXC_DESC": exc_desc,
        })
        self._closed = True
        self._handle.close()
        with suppress(Exception):  # pragma: no cover - raw stream may already be closed
            self._raw.close()
        replace_atomic(self._pending_path, self.path)
        return self.path

    def __enter__(self) -> StdfWriter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close(exc_desc="" if exc is None else repr(exc))

    def status(self) -> dict[str, Any]:
        """Writer statistics (used by tests and the GUI)."""
        return {
            "path": str(self.path),
            "parts": self.part_count,
            "retests": self.retest_count,
            "hard_bins": dict(sorted(self._hard_bins.items())),
            "soft_bins": dict(sorted(self._soft_bins.items())),
        }


class StdfSink:
    """Datalog sink writing one STDF file per wafer/tray."""

    def __init__(self, *, output_dir: str | Path, session: Any, stage: str,
                 byte_order: str = "little", gzip_output: bool = False,
                 write_wcr: bool = False, write_fail_pin: bool = False,
                 site_count: int = 1) -> None:
        self.output_dir = Path(output_dir)
        self.session = session
        self.stage = stage
        self.byte_order = byte_order
        self.gzip_output = gzip_output
        self.wcr_enabled = bool(write_wcr)
        self.write_fail_pin = write_fail_pin
        self.site_count = site_count
        self.writer: StdfWriter | None = None
        self.path: Path | None = None
        self.files: list[Path] = []
        # site threads write into the same file: every part block must be atomic
        self._lock = threading.RLock()

    def on_unit_start(self, unit: Any) -> None:
        """Open a new STDF file for the unit."""
        with self._lock:
            self._open_unit(unit)

    def _open_unit(self, unit: Any) -> None:
        lot_id = getattr(getattr(self.session, "lot", None), "lot_id", self.session.name)
        kind = "wafer" if unit.kind == "wafer" else "tray"
        # deterministic file name: rerunning into the same directory overwrites
        # the previous attempt instead of piling up timestamped copies
        name = f"{lot_id}_{unit.unit_id}_{self.stage}.stdf"
        self.writer = StdfWriter(
            self.output_dir / name,
            byte_order=self.byte_order,
            gzip_output=self.gzip_output,
            write_wcr=self.wcr_enabled,
            write_fail_pin=self.write_fail_pin,
            site_count=self.site_count,
            tester_name=getattr(self.session.tester, "name", "PYATE"),
            tester_model=getattr(self.session.tester, "model", "PYATE-SIM"),
            hand_type="PROBER" if kind == "wafer" else "HANDLER",
            card_id=getattr(self.session.loadboard, "board_id", "CARD1")
            if getattr(self.session, "loadboard", None) else "CARD1",
            pin_maps={site: {pin: entry.channel_of(site, bit) for pin, (entry, bit)
                             in self.session.channel_map.expand().items()}
                      for site in range(1, self.site_count + 1)},
        )
        self.path = self.writer.path
        self.writer.write_atr(f"pyate run {self.session.path}")
        self.writer.write_mir(
            lot_id=str(lot_id),
            sublot=str(getattr(getattr(self.session, "lot", None), "sublot", "")),
            part_type=self.session.dut_profile.model,
            job_name=self.session.program.program,
            stage=self.stage,
            operator=self.session.spec.name if hasattr(self.session, "spec") else "PYATE",
            temperature=self.session.temperature(),
            serial_number=str(getattr(getattr(self.session, "tester", None), "model", "")),
        )
        self.writer.write_sdr(self.site_count)
        if kind == "wafer":
            self.writer.write_wir(unit.unit_id)
            wafer = getattr(self.session, "wafer", None)
            self.writer.write_wcr_record(
                wafer_size_mm=getattr(wafer, "diameter_mm", None),
                die_w_mm=wafer.die_size_mm[0] if wafer else None,
                die_h_mm=wafer.die_size_mm[1] if wafer else None,
                center_x=wafer.center_die[0] if wafer else 0,
                center_y=wafer.center_die[1] if wafer else 0,
                notch=wafer.notch if wafer else "bottom",
                wafer_units=3,
            )

    def on_part(self, part: PartResult, unit: Any) -> None:
        """Write PIR, every test record and PRR for a part."""
        if self.writer is None or part.skipped:
            return
        with self._lock:
            # superseded retest attempts are written as their own PIR/PRR pair so
            # the STDF keeps the full test history (PART_FLG retest bit set).
            for previous in part.extra.get("previous_attempts", []) or []:
                self.writer.start_part(previous)
                for result in previous.results:
                    self.writer.write_result(result)
                self.writer.end_part(previous)
            self.writer.start_part(part)
            for result in part.results:
                self.writer.write_result(result)
            self.writer.end_part(part)

    def on_unit_end(self, summary: Any) -> None:
        """Write the summary records and close the file."""
        with self._lock:
            self._close_unit(summary)

    def _close_unit(self, summary: Any) -> None:
        if self.writer is None:
            return
        binning = getattr(self.session, "binning", None)
        hard_names: dict[int, str] = {}
        hard_pass: dict[int, str] = {}
        soft_names: dict[int, str] = {}
        soft_pass: dict[int, str] = {}
        if binning is not None:
            hard_names = {item.hbin: item.name for item in binning.hard_bins}
            soft_names = {item.sbin: item.name for item in binning.soft_bins}
            soft_pass = {item.sbin: item.pass_fail for item in binning.soft_bins}
            hard_pass = {
                item.hbin: ("P" if item.hbin == binning.pass_bin else "F")
                for item in binning.hard_bins
            }
        self.writer.write_summaries(
            hard_bin_names=hard_names, soft_bin_names=soft_names,
            hard_bin_pass=hard_pass, soft_bin_pass=soft_pass,
        )
        if getattr(summary, "kind", "") == "wafer":
            self.writer.write_wrr(wafer_id=summary.unit_id, good=summary.passed)
        self.writer.close()
        self.files.append(self.writer.path)
        self.writer = None

    def close(self) -> None:
        """Close any writer left open by an aborted run."""
        with self._lock:
            self._close_aborted()

    def _close_aborted(self) -> None:
        if self.writer is not None:
            try:
                self.writer.close(disposition="A", exc_desc="run aborted")
            finally:
                self.files.append(self.writer.path)
                self.writer = None
