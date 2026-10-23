"""Generate the demo_cp / demo_ft configuration trees.

Run with::

    python tools/gen_demo_configs.py

The script is idempotent: it rewrites ``configs/demo_cp`` and
``configs/demo_ft`` from the same source of truth used by the tests.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pyate.pattern.generators import (  # noqa: E402
    composite_pattern,
    counter_loop_pattern,
    shift_pattern,
)

SIGNAL_PINS = ["CLK", "RSTN", "EN", "SI", "SO"] + [f"Q{i}" for i in range(8)] + [
    f"S{i}" for i in range(8)
]


def dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def channel_map(sites: int, stride: int = 48) -> dict[str, Any]:
    def base(site: int) -> int:
        return 1 + (site - 1) * stride

    pins: list[dict[str, Any]] = [
        {"pin": "VDD", "type": "power", "sites": {site: base(site) for site in range(1, sites + 1)}},
        {"pin": "VSS", "type": "power", "sites": {site: base(site) + 1 for site in range(1, sites + 1)}},
    ]
    for index, name in enumerate(SIGNAL_PINS):
        direction = "out" if (name == "SO" or (name[0] in "QS" and name[1:].isdigit())) else "in"
        pins.append({
            "pin": name,
            "type": direction,
            "sites": {site: base(site) + 2 + index for site in range(1, sites + 1)},
        })
    return {"pins": pins}


def write_common(directory: Path, *, sites: int) -> None:
    dump(directory / "levels.yaml", {
        "default_set": "lv_nominal",
        "sets": [{
            "name": "lv_nominal", "vil": -0.3, "vih": 2.0, "vol": 0.2, "voh": 2.8,
            "vterm": 0.0, "iol": 0.004, "ioh": -0.004,
            "vdd": {"nom": 3.3, "min": 2.7, "max": 3.6},
        }],
    })
    dump(directory / "timing.yaml", {
        "default_set": "tset_fast",
        "sets": [
            {"name": "tset_fast", "period_ns": 50.0,
             "edges": {"drive_on": 0.0, "data": 5.0, "return": 0.0, "compare": 35.0}},
            {"name": "tset_slow", "period_ns": 100.0,
             "edges": {"drive_on": 0.0, "data": 5.0, "return": 0.0, "compare": 70.0}},
        ],
    })
    dump(directory / "dut_profile.yaml", {
        "model": "composite",
        "params": {"counter_bits": 8, "shift_bits": 8},
        "defects": [],
        "yield": {
            "pass": 0.90,
            "edge_fail_boost": 1.8,
            "cluster_prob": 0.03,
            "defect_mix": {
                "stuck_at": 0.42, "open": 0.18, "short": 0.14,
                "leakage": 0.12, "high_idd": 0.09, "slow": 0.05,
            },
        },
        "process": {"vth_sigma": 0.015, "idd_sigma": 0.03, "leak_sigma": 0.12},
    })
    dump(directory / "binning.yaml", {
        "pass_bin": 1,
        "default_fail_bin": 9,
        "soft_bins": [
            {"sbin": 1, "name": "PASS", "pass_fail": "P"},
            {"sbin": 2, "name": "CONTINUITY", "pass_fail": "F"},
            {"sbin": 3, "name": "LEAKAGE", "pass_fail": "F"},
            {"sbin": 4, "name": "IDD", "pass_fail": "F"},
            {"sbin": 5, "name": "DC_LEVEL", "pass_fail": "F"},
            {"sbin": 6, "name": "FUNCTIONAL", "pass_fail": "F"},
            {"sbin": 7, "name": "SPEED", "pass_fail": "F"},
            {"sbin": 8, "name": "MEMORY", "pass_fail": "F"},
            {"sbin": 9, "name": "OTHER", "pass_fail": "F"},
        ],
        "hard_bins": [
            {"hbin": 1, "name": "BIN1_PASS"},
            {"hbin": 2, "name": "BIN2_CONT"},
            {"hbin": 3, "name": "BIN3_LEAK"},
            {"hbin": 4, "name": "BIN4_IDD"},
            {"hbin": 5, "name": "BIN5_DC"},
            {"hbin": 6, "name": "BIN6_FUNC"},
            {"hbin": 7, "name": "BIN7_SPEED"},
            {"hbin": 8, "name": "BIN8_MEM"},
            {"hbin": 9, "name": "BIN9_OTHER"},
        ],
        "rules": [{"sbin": index, "hbin": index} for index in range(1, 10)],
        "bin_priority": [9, 7, 6, 5, 4, 3, 2],
        "fail_code_bins": {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "99": 9},
    })
    dump(directory / "loadboard.yaml", {
        "board_id": "LB-DEMO-01",
        "site_count": sites,
        "relay_map": {"pmu_bus": "PMU1", "dps_bus": "DPS1"},
    })
    dump(directory / "stdf_options.yaml", {
        "output_dir": "out",
        "gzip": False,
        "byte_order": "little",
        "cpu_type": 2,
        "write_wcr": False,
        "write_csv": True,
        "write_datalog": True,
    })

    rows = [
        ("1100", "leakage_il/ih", "", "250e-9", "A", "0", "*", "-999", "999"),
        ("2000", "idd_static", "0", "260e-6", "A", "0", "*", "0", "999"),
        ("2100", "idd_dynamic", "0", "5e-3", "A", "0", "*", "0", "999"),
        ("3000", "functional_cnt", "", "", "", "0", "*", "-999", "999"),
        ("3100", "functional_loop", "", "", "", "0", "*", "-999", "999"),
        ("4000", "fmax", "60", "", "MHz", "0", "*", "-999", "999"),
        ("4100", "vdd_min", "", "3.05", "V", "0", "*", "-999", "999"),
        ("9000", "delay", "", "", "ms", "0", "*", "-999", "999"),
    ]
    with (directory / "limits.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["test_num", "name", "lo", "hi", "unit", "scale", "stage",
                         "temp_min", "temp_max"])
        writer.writerows(rows)


def write_patterns(directory: Path) -> None:
    target = directory / "patterns"
    target.mkdir(parents=True, exist_ok=True)
    (target / "cnt_basic.pat").write_text(
        composite_pattern(counter_bits=8, shift_bits=8, cycles=20, name="cnt_basic"),
        encoding="utf-8",
    )
    (target / "cnt_loop.pat").write_text(
        counter_loop_pattern(bits=8, name="cnt_loop"), encoding="utf-8"
    )
    (target / "shift_walk1.pat").write_text(
        shift_pattern(bits=8, name="shift_walk1"), encoding="utf-8"
    )


def cp_program() -> dict[str, Any]:
    return {
        "program": "DEMO_CNT8",
        "revision": "1.0",
        "flows": {
            "CP1": [
                {"item": "continuity", "test_num": 1000,
                 "params": {"fail_code": 2}, "on_fail": {"action": "stop", "bin": 2}},
                {"item": "leakage_ih_il", "test_num": 1100,
                 "params": {"fail_code": 3}, "on_fail": {"action": "continue"}},
                {"item": "idd_static", "test_num": 2000,
                 "params": {"fail_code": 4}, "on_fail": {"action": "continue"}},
                {"item": "idd_dynamic", "test_num": 2100,
                 "params": {"fail_code": 4, "freq_mhz": 10.0}, "on_fail": {"action": "continue"}},
                {"item": "vol_voh", "test_num": 2200,
                 "params": {"fail_code": 5}, "on_fail": {"action": "continue"}},
                {"group": "functional_suite", "items": [
                    {"item": "functional", "test_num": 3000,
                     "params": {"pattern": "cnt_basic.pat", "fail_code": 6},
                     "on_fail": {"action": "continue"}},
                    {"item": "functional", "test_num": 3100,
                     "params": {"pattern": "cnt_loop.pat", "max_cycles": 64, "fail_code": 6},
                     "on_fail": {"action": "continue"}},
                ]},
                {"item": "fmax_search", "test_num": 4000,
                 "params": {"pattern": "cnt_loop.pat", "period_min_ns": 4.0,
                            "period_max_ns": 100.0, "resolution_ns": 2.0,
                            "cycles": 128, "fail_code": 7},
                 "on_fail": {"action": "continue"}},
                {"item": "vdd_min_search", "test_num": 4100,
                 "params": {"pattern": "cnt_loop.pat", "vdd_start": 3.6,
                            "vdd_stop": 2.4, "resolution": 0.05, "cycles": 128,
                            "fail_code": 7},
                 "on_fail": {"action": "continue"}},
            ],
            "CP2": [
                {"item": "continuity", "test_num": 1000,
                 "params": {"fail_code": 2}, "on_fail": {"action": "stop", "bin": 2}},
                {"item": "leakage_ih_il", "test_num": 1100,
                 "params": {"fail_code": 3}, "on_fail": {"action": "continue"}},
                {"item": "functional", "test_num": 3000,
                 "params": {"pattern": "cnt_basic.pat", "fail_code": 6},
                 "on_fail": {"action": "continue"}},
            ],
        },
    }


def ft_program() -> dict[str, Any]:
    return {
        "program": "DEMO_CNT8_FT",
        "revision": "1.0",
        "flows": {
            "FT1": [
                {"item": "continuity", "test_num": 1000,
                 "params": {"fail_code": 2}, "on_fail": {"action": "stop", "bin": 2}},
                {"item": "idd_static", "test_num": 2000,
                 "params": {"fail_code": 4}, "on_fail": {"action": "continue"}},
                {"item": "functional", "test_num": 3000,
                 "params": {"pattern": "cnt_basic.pat", "fail_code": 6},
                 "on_fail": {"action": "continue"}},
                {"item": "fmax_search", "test_num": 4000,
                 "params": {"pattern": "cnt_loop.pat", "period_min_ns": 4.0,
                            "period_max_ns": 100.0, "resolution_ns": 2.0,
                            "cycles": 128, "fail_code": 7},
                 "on_fail": {"action": "continue"}},
            ],
        },
    }


def build_cp() -> None:
    directory = ROOT / "configs" / "demo_cp"
    write_common(directory, sites=4)
    write_patterns(directory)
    dump(directory / "tester.yaml", {
        "name": "SIM-ATE-CP",
        "model": "PYATE-SIM",
        "num_sites_max": 8,
        "digital_channels": 256,
        "dps_channels": 16,
        "pmu_channels": 64,
        "max_vector_rate_mhz": 200.0,
        "vector_depth": 1000000,
        "overhead_ms": 0.4,
        "simulation": {"noise_sigma": 0.002, "sim_time_scale": 0.0, "seed": 20261021},
    })
    dump(directory / "channel_map.yaml", channel_map(4))
    dump(directory / "wafer.yaml", {
        "diameter_mm": 300.0,
        "die_size_mm": [10.0, 10.0],
        "edge_exclusion_mm": 3.0,
        "notch": "bottom",
        "center_die": [0, 0],
        "skip_list": [],
    })
    dump(directory / "prober.yaml", {
        "index_time_ms": 250.0,
        "layout": {"cols": 2, "rows": 2},
        "wafer_change_time_s": 12.0,
        "contact_fail_prob": 0.002,
        "temperature": 25.0,
        "scan": "serpentine",
    })
    dump(directory / "lot.yaml", {
        "lot_id": "LOT-CP-0001",
        "sublot": "A",
        "wafers": ["W01", "W02"],
        "flow": ["CP1", "CP2", "FT1"],
        "trays": 0,
        "parts_per_tray": 0,
    })
    dump(directory / "testprogram.yaml", cp_program())
    dump(directory / "session.yaml", {
        "name": "demo_cp",
        "description": "CP demo: 300 mm wafer, 4 sites, counter+shift register DUT",
        "program": "testprogram.yaml",
        "tester": "tester.yaml",
        "channel_map": "channel_map.yaml",
        "loadboard": "loadboard.yaml",
        "levels": "levels.yaml",
        "timing": "timing.yaml",
        "limits": "limits.csv",
        "binning": "binning.yaml",
        "dut_profile": "dut_profile.yaml",
        "wafer": "wafer.yaml",
        "lot": "lot.yaml",
        "prober": "prober.yaml",
        "stdf": "stdf_options.yaml",
        "run": {
            "stage": "CP1",
            "sites": 4,
            "seed": 20261021,
            "output_dir": "out",
            "parallel_mode": "thread",
            "disabled_sites": [],
        },
    })


def build_ft() -> None:
    directory = ROOT / "configs" / "demo_ft"
    write_common(directory, sites=8)
    write_patterns(directory)
    dump(directory / "tester.yaml", {
        "name": "SIM-ATE-FT",
        "model": "PYATE-SIM",
        "num_sites_max": 8,
        "digital_channels": 512,
        "dps_channels": 16,
        "pmu_channels": 128,
        "max_vector_rate_mhz": 200.0,
        "vector_depth": 1000000,
        "overhead_ms": 0.5,
        "simulation": {"noise_sigma": 0.002, "sim_time_scale": 0.0, "seed": 20261021},
    })
    dump(directory / "channel_map.yaml", channel_map(8))
    dump(directory / "handler.yaml", {
        "layout": {"cols": 4, "rows": 2},
        "cycle_time_ms": 180.0,
        "jam_prob": 0.001,
        "tray_capacity": 500,
        "temperature": 25.0,
        "jam_recovery_ms": 30000.0,
    })
    dump(directory / "lot.yaml", {
        "lot_id": "LOT-FT-0001",
        "sublot": "B",
        "wafers": [],
        "flow": ["FT1"],
        "trays": 2,
        "parts_per_tray": 500,
    })
    dump(directory / "testprogram.yaml", ft_program())
    dump(directory / "session.yaml", {
        "name": "demo_ft",
        "description": "FT demo: 8 sites, 2 trays x 500 parts, handler jam/contact fail",
        "program": "testprogram.yaml",
        "tester": "tester.yaml",
        "channel_map": "channel_map.yaml",
        "loadboard": "loadboard.yaml",
        "levels": "levels.yaml",
        "timing": "timing.yaml",
        "limits": "limits.csv",
        "binning": "binning.yaml",
        "dut_profile": "dut_profile.yaml",
        "lot": "lot.yaml",
        "handler": "handler.yaml",
        "stdf": "stdf_options.yaml",
        "run": {
            "stage": "FT1",
            "sites": 8,
            "seed": 7,
            "output_dir": "out",
            "parallel_mode": "thread",
            "disabled_sites": [],
        },
    })


def main() -> None:
    build_cp()
    build_ft()
    print("generated configs/demo_cp and configs/demo_ft")


if __name__ == "__main__":
    main()
