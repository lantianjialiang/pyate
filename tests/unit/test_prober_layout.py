"""Probe-card layout and scan order actually change the touchdown path."""

from pyate.config.models import ProberConfig, SiteLayout, WaferConfig
from pyate.scenario.prober_sim import ProberSimulator
from pyate.scenario.wafer_map import WaferDie, WaferMap


def _wafer() -> WaferMap:
    wafer = WaferMap(WaferConfig(diameter_mm=20, die_size_mm=(10, 10), edge_exclusion_mm=0))
    wafer.dies = {
        (x, y): WaferDie(x, y, f"P{x}_{y}")
        for x in range(4)
        for y in range(2)
    }
    wafer._bounds = (0, 3, 0, 1)
    return wafer


def _first(scan: str, cols: int = 2, rows: int = 2) -> list[tuple[int, int]]:
    config = ProberConfig(layout=SiteLayout(cols=cols, rows=rows), scan=scan, contact_fail_prob=0)
    touches = list(ProberSimulator(_wafer(), config, sites=4).touchdowns())
    return [(x, y) for _site, x, y, _part in touches[0].sites]


def test_row_scan_fills_left_to_right() -> None:
    assert _first("row") == [(0, 0), (1, 0), (0, 1), (1, 1)]


def test_column_scan_fills_top_to_bottom() -> None:
    assert _first("column") == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_layout_larger_than_sites_shrinks_and_is_reported() -> None:
    config = ProberConfig(layout=SiteLayout(cols=4, rows=4), scan="row", contact_fail_prob=0)
    touch = next(ProberSimulator(_wafer(), config, sites=4).touchdowns())
    assert touch.xtra["configured_layout"] == (4, 4)
    assert touch.xtra["layout"] != (4, 4)
    assert touch.xtra["scan"] == "row"
