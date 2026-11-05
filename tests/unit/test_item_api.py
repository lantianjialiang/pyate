"""``pyate.api.run_item``: one test item, one site, no lot walk."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import CONFIGS
from pyate.api import EventBus, EventKind, Session, run_item

SHMOO_SESSION = CONFIGS / "demo_shmoo" / "session.yaml"
CP_SESSION = CONFIGS / "demo_cp" / "session.yaml"


def test_run_item_reports_one_site_and_publishes_a_result() -> None:
    seen: list = []
    bus = EventBus()
    bus.subscribe("*", seen.append)
    report = run_item(Session(SHMOO_SESSION), name="idd_static", test_num=2000, events=bus)
    assert report.passed
    assert report.test_num == 2000
    assert report.stage == "CP1"
    assert report.sites == (1,)
    assert len(report.values) == 1
    row = report.values[0]
    assert row["site"] == 1
    assert row["test_name"] == "idd_static"
    assert row["value"] == pytest.approx(180e-6, rel=0.2)
    # the datalog needs TEST_RESULT, nothing else: no part must be invented
    kinds = [event.kind for event in seen]
    assert EventKind.TEST_RESULT.value in kinds
    assert EventKind.PART_END.value not in kinds
    assert "idd_static" in report.text()
    assert report.to_dict()["sites"] == [1]


def test_run_item_can_run_every_site_of_a_multi_site_loadboard() -> None:
    report = run_item(Session(CP_SESSION), name="idd_static", test_num=2000, sites=[1, 2])
    assert report.sites == (1, 2)
    assert [row["site"] for row in report.values] == [1, 2]


def test_run_item_defaults_to_the_enabled_sites() -> None:
    session = Session(CP_SESSION)
    session.update({"run.sites": 3, "run.disabled_sites": [2]})
    report = run_item(session, name="idd_static", test_num=2000)
    assert report.sites == (1, 3)


def test_run_item_uses_the_parameters_it_is_given() -> None:
    """The GUI passes the parameter form, so unsaved edits must take effect."""
    session = Session(SHMOO_SESSION)
    high = run_item(session, name="idd_static", test_num=2000, params={"vdd": 3.3})
    low = run_item(Session(SHMOO_SESSION), name="idd_static", test_num=2000, params={"vdd": 2.6})
    assert high.values[0]["value"] > low.values[0]["value"]


def test_run_item_applies_the_limits_csv() -> None:
    """Limits come from the session, not from the item defaults."""
    session = Session(SHMOO_SESSION)
    loaded = session.load()
    lo_limit, hi_limit = next(row for row in loaded.limits.limits
                              if row.test_num == 2000).physical_bounds()
    report = run_item(session, name="idd_static", test_num=2000,
                      params={"hi": 1e-9, "lo": None})
    row = report.values[0]
    # the CSV row wins over the impossible hi=1e-9 the caller passed
    assert row["hi_limit"] == pytest.approx(hi_limit)
    assert row["lo_limit"] == pytest.approx(lo_limit)
    assert row["passed"] is True


def test_run_item_reports_a_disabled_node() -> None:
    session = Session(SHMOO_SESSION)
    program = session.load().program
    node = next(node for node in program.flow("CP1") if node.item == "shmoo")
    node.enabled = False
    report = run_item(session, name="shmoo", test_num=9100)
    assert not report.passed
    assert report.values == ()
    assert "unchecked" in report.text()


def test_run_item_reports_an_unknown_item() -> None:
    report = run_item(Session(SHMOO_SESSION), name="does_not_exist", test_num=1)
    assert "not found" in report.text()


def test_run_item_reports_no_enabled_site() -> None:
    session = Session(SHMOO_SESSION)
    session.update({"run.disabled_sites": [1]})
    report = run_item(session, name="idd_static", test_num=2000)
    assert "no site enabled" in report.text()


def test_run_item_is_a_nominal_die(tmp_path: Path) -> None:
    """Same plot whatever the seed: characterization must be reproducible."""
    import shutil

    target = tmp_path / "demo_shmoo"
    shutil.copytree(CONFIGS / "demo_shmoo", target)
    first = run_item(Session(target / "session.yaml"), name="shmoo", test_num=9100)
    session = Session(target / "session.yaml")
    session.update({"run.seed": 999})
    second = run_item(session, name="shmoo", test_num=9100)
    assert first.values[0]["value"] == second.values[0]["value"]
