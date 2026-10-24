"""Input-tray slot order and equipment effectiveness."""

from __future__ import annotations

from pyate.api import Event
from pyate.config.models import HandlerConfig, SiteLayout
from pyate.engine.metrics import EquipmentStats
from pyate.gui.viewmodels.run_vm import RunViewModel
from pyate.scenario.handler_sim import HandlerSimulator


def _handler(parts: int, sites: int) -> list:
    config = HandlerConfig(
        layout=SiteLayout(cols=sites, rows=1),
        tray_cols=5,
        tray_rows=2,
        cycle_time_ms=180.0,
        jam_prob=0.0,
        contact_fail_prob=0.0,
    )
    names = [f"P{index:02d}" for index in range(parts)]
    return list(HandlerSimulator(names, config, sites, tray_id="T01").touchdowns())


def test_picks_fill_the_tray_left_to_right_and_mask_the_tail() -> None:
    touches = _handler(10, 4)
    assert len(touches) == 3
    assert [(site, x, y) for site, x, y, _pid in touches[0].sites] == [
        (1, 0, 0), (2, 1, 0), (3, 2, 0), (4, 3, 0),
    ]
    assert [(site, x, y) for site, x, y, _pid in touches[1].sites] == [
        (1, 4, 0), (2, 0, 1), (3, 1, 1), (4, 2, 1),
    ]
    assert [(site, x, y, pid) for site, x, y, pid in touches[2].sites] == [
        (1, 3, 1, "P08"), (2, 4, 1, "P09"),
    ]
    assert touches[2].xtra["masked_sites"] == [3, 4]
    assert touches[0].xtra["masked_sites"] == []
    seen = [entry[3] for touch in touches for entry in touch.sites]
    assert seen == [f"P{index:02d}" for index in range(10)]


def test_full_cadence_is_oee_one_and_a_tail_or_jam_lowers_it() -> None:
    full = EquipmentStats(sites=4)
    full.add_touchdown(tested=4, passed=4, test_ms=100.0, index_ms=50.0)
    full.add_touchdown(tested=4, passed=4, test_ms=100.0, index_ms=50.0)
    assert full.performance == 1.0
    assert full.availability == 1.0
    assert full.oee == 1.0
    assert full.uph == full.theoretical_uph

    tail = EquipmentStats(sites=4)
    tail.add_touchdown(tested=2, passed=2, test_ms=100.0, index_ms=50.0)
    assert tail.performance == 0.5
    assert tail.oee == 0.5

    jammed = EquipmentStats(sites=4)
    jammed.add_touchdown(tested=4, passed=3, test_ms=100.0, index_ms=50.0, jam_ms=150.0)
    assert jammed.jams == 1
    assert jammed.availability == 0.5
    assert jammed.quality == 0.75
    assert jammed.oee == 0.5 * 1.0 * 0.75
    assert jammed.uph < jammed.gross_uph


def test_tray_view_picks_a_slot_into_its_hard_bin() -> None:
    model = RunViewModel()
    model.on_event(Event("UNIT_START", {
        "unit": "T01", "kind": "tray", "tray_cols": 5, "tray_rows": 2, "tray_parts": 10,
    }))
    model.on_event(Event("TOUCHDOWN", {
        "unit": "T01", "kind": "tray", "touchdown": 1,
        "slots": [
            {"site": 1, "x": 0, "y": 0, "part_id": "P00"},
            {"site": 2, "x": 1, "y": 0, "part_id": "P01"},
        ],
        "masked_sites": [3, 4],
    }))
    view = model.tray_view()
    assert view["cols"] == 5 and view["total"] == 10
    testing = [slot for slot in view["slots"] if slot["state"] == "testing"]
    assert {slot["part_id"] for slot in testing} == {"P00", "P01"}
    assert {site["site"]: site["status"] for site in view["sites"]}[3] == "masked"

    model.on_event(Event("PART_END", {"part": {
        "part_id": "P00", "site": 1, "x": 0, "y": 0,
        "soft_bin": 1, "hard_bin": 1, "passed": True, "skipped": False,
    }}))
    model.on_event(Event("PART_END", {"part": {
        "part_id": "P00", "site": 1, "x": 0, "y": 0,
        "soft_bin": 1, "hard_bin": 1, "passed": True, "skipped": False, "retest": True,
    }}))
    model.on_event(Event("PROGRESS", {
        "touchdowns": 3, "uph": 100.0,
        "equipment": {
            "uph": 120.0, "gross_uph": 140.0, "theoretical_uph": 160.0,
            "availability": 0.9, "performance": 0.8, "quality": 0.7,
            "oee": 0.504, "jams": 2,
        },
    }))
    view = model.tray_view()
    picked = next(slot for slot in view["slots"] if slot["part_id"] == "P00")
    assert picked["state"] == "picked"
    assert view["output"] == [{"bin": 1, "count": 1}]
    assert view["jams"] == 2
    snap = model.snapshot()
    assert snap["oee"] == 50.4
    assert snap["uph"] == 120.0
    assert snap["jams"] == 2
