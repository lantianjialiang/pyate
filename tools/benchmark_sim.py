"""Reproducible simulator scale probes; run each case in a fresh process.

Examples: python tools/benchmark_sim.py parts --parts 100000 --sites 8
          python tools/benchmark_sim.py sites --touchdowns 1000 --repeats 3
          python tools/benchmark_sim.py pattern --vectors 1000000
          python tools/benchmark_sim.py gui --events 100000 --rate 10000

Reports include real elapsed time and process peak RSS, not modeled tester time.
The parts probe uses SQLite recovery storage and no optional datalog sinks.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import itertools
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def peak_rss_mib() -> float:
    if os.name == "nt":
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_working_set", "working_set", "peak_paged", "paged",
                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak_working_set / 2**20
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (2**20 if sys.platform == "darwin" else 1024)


def part_probe(args, *, repeat: int = 0) -> dict:
    from pyate.config.loader import load_session
    from pyate.engine.controller import RunController

    count = args.parts if args.case == "parts" else args.sites * args.touchdowns
    session = load_session(ROOT / "configs/demo_ft/session.yaml", {
        "run.sites": args.sites, "run.parallel_mode": "thread", "run.max_retries": 0,
        "lot.trays": 1, "lot.parts_per_tray": count,
        "handler.jam_prob": 0, "handler.contact_fail_prob": 0,
        "program.flows.FT1": [{"item": "delay_ms", "test_num": 9000, "params": {"ms": 0}}],
    })
    controller = RunController(session, output_dir=args.output / f"run_{repeat}")
    start = time.perf_counter()
    summary = controller.run()
    elapsed = time.perf_counter() - start
    digest = hashlib.sha256()
    result_count = 0
    for part in controller.iter_part_results("T01"):
        instance = part.extra["physical_instance"]
        digest.update(json.dumps([part.part_id, part.soft_bin, instance,
                                  [(row.test_num, row.value, row.passed) for row in part.results]],
                                 sort_keys=True).encode())
        result_count += 1
    assert summary.total_tested == result_count == count
    assert len(list(controller.iter_part_results("T01", offset=max(0, count - 10), limit=10))) == min(count, 10)
    return {"parts": count, "sites": args.sites, "touchdowns": summary.units[0].meta["touchdowns"],
            "seconds": elapsed, "parts_per_second": count / elapsed,
            "digest": digest.hexdigest(), "status": summary.status,
            "population_entries": len(controller.population),
            "history_storage_bytes": (args.output / f"run_{repeat}/results.sqlite3").stat().st_size}


def pattern_probe(args) -> dict:
    from pyate.config.models import DutProfile, LevelsSet, TimingSet
    from pyate.dut.registry import create_dut
    from pyate.pattern.engine import PatternEngine
    from pyate.pattern.parser import parse_pattern_text

    body = "CLK=C RSTN=1 EN=0;\n"
    source = "pattern benchmark;\npins CLK, RSTN, EN;\n"
    source += body * args.vectors if args.expanded else f"repeat {args.vectors} {body}"
    source += "halt;\n"
    start = time.perf_counter()
    pattern = parse_pattern_text(source)
    parsed = time.perf_counter() - start
    dut = create_dut(DutProfile(model="counter"), sample_yield=False)
    dut.power(3.3)
    engine = PatternEngine(dut, LevelsSet(name="lv"), TimingSet(name="t"))
    start = time.perf_counter()
    result = engine.run(pattern, max_cycles=args.vectors + 1)
    executed = time.perf_counter() - start
    assert result.passed and result.cycles == args.vectors
    return {"vectors": args.vectors, "expanded_source": args.expanded, "source_bytes": len(source),
            "stored_vectors": pattern.vector_count, "parse_seconds": parsed,
            "execute_seconds": executed, "vectors_per_second": args.vectors / executed}


def gui_probe(args) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    from pyate.api import Event
    from pyate.gui.panels import RunPanel
    from pyate.gui.viewmodels.run_vm import RunViewModel

    app = QApplication.instance() or QApplication([])
    vm = RunViewModel()
    panel = RunPanel(vm)
    panel.resize(1400, 900)
    paints = []
    latest_sent = [0.0]
    failures = []

    class PaintCounter(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                now = time.perf_counter()
                if latest_sent[0]:
                    paints.append((now, now - latest_sent[0]))
            return False

    counter = PaintCounter(panel)
    panel.datalog.viewport().installEventFilter(counter)
    panel.show()
    app.processEvents()
    start = time.perf_counter()

    def produce():
        try:
            for index in range(args.events):
                latest_sent[0] = time.perf_counter()
                vm.on_event(Event("TEST_RESULT", {"part_id": str(index), "result": {
                    "test_num": 1, "test_name": "IDD", "site": index % 8 + 1,
                    "value": float(index % 100), "passed": True,
                }}, seq=index + 1))
                if index % 100 == 99:
                    delay = start + (index + 1) / args.rate - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
        except Exception as exc:
            failures.append(repr(exc))

    producer = threading.Thread(target=produce, name="benchmark-producer")
    producer.start()
    while producer.is_alive():
        app.processEvents()
        time.sleep(0.001)
    producer.join()
    elapsed = time.perf_counter() - start
    app.processEvents()
    panel.timer.stop()
    panel.close()
    assert not failures, failures
    assert vm.summary_rows()[0].count == args.events
    intervals = [later[0] - earlier[0] for earlier, later in itertools.pairwise(paints)]
    return {"events": args.events, "requested_events_per_second": args.rate,
            "seconds": elapsed, "events_per_second": args.events / elapsed,
            "viewport_paints": len(paints), "paint_fps": len(paints) / elapsed,
            "paint_interval_p95_ms": statistics.quantiles(intervals, n=20)[18] * 1000 if len(intervals) > 1 else None,
            "latest_event_to_paint_p95_ms": statistics.quantiles([v[1] for v in paints], n=20)[18] * 1000 if len(paints) > 1 else None,
            "retained_parts": len(vm._history_keys), "retained_rows": len(vm.rows),
            "qt_platform": app.platformName()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("parts", "sites", "pattern", "gui"))
    parser.add_argument("--output", type=Path, default=ROOT / "out/benchmark")
    parser.add_argument("--parts", type=int, default=100_000)
    parser.add_argument("--sites", type=int, default=8)
    parser.add_argument("--touchdowns", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--vectors", type=int, default=1_000_000)
    parser.add_argument("--expanded", action="store_true", help="parse one source line per vector")
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--rate", type=int, default=10_000)
    args = parser.parse_args()
    if min(args.parts, args.sites, args.touchdowns, args.repeats, args.vectors, args.events, args.rate) <= 0:
        parser.error("sizes and rates must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {"python": platform.python_version(), "os": platform.platform(),
                "cpu": platform.processor(), "logical_cpus": os.cpu_count(), "dependencies": {}}
    for package in ("pydantic", "pytest", "PySide6-Essentials"):
        with contextlib.suppress(PackageNotFoundError):
            metadata["dependencies"][package] = version(package)
    for label, command in (("commit", ["git", "rev-parse", "HEAD"]),
                           ("dirty_status", ["git", "status", "--porcelain"])):
        metadata[label] = subprocess.check_output(command, cwd=ROOT, text=True).strip()
    if args.case == "parts":
        result = part_probe(args)
    elif args.case == "sites":
        runs = [part_probe(args, repeat=index) for index in range(args.repeats)]
        assert len({run["digest"] for run in runs}) == 1
        result = {"runs": runs, "identical": True}
    elif args.case == "pattern":
        result = pattern_probe(args)
    else:
        result = gui_probe(args)
    report = {"case": args.case, "environment": metadata, "result": result, "peak_rss_mib": peak_rss_mib()}
    path = args.output / f"{args.case}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "result": result, "peak_rss_mib": report["peak_rss_mib"]}, indent=2))


if __name__ == "__main__":
    main()
