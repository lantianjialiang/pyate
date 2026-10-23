"""Component tests for the flow executor: limits, binning, branches, control."""

from __future__ import annotations

import threading

import pytest

from pyate.config.loader import load_session
from pyate.config.models import FlowNode
from pyate.dut.base import Defect
from pyate.dut.registry import create_dut
from pyate.engine.binning import BinningEngine
from pyate.engine.context import SiteContext
from pyate.engine.context import TestResult as Measurement
from pyate.engine.events import EventBus, EventKind
from pyate.engine.executor import FlowExecutor, RunAborted
from pyate.engine.flow import ExpressionError, SafeExpression, evaluate_branch, iter_flow_items
from pyate.hal.simulated import SimulatedHAL
from pyate.pattern.parser import load_pattern
from pyate.testitems.base import LimitsTable

from conftest import CONFIGS

SESSION = CONFIGS / "demo_cp"


def _node(**kwargs) -> FlowNode:
    return FlowNode.model_validate(kwargs)


@pytest.fixture()
def rig():
    session = load_session(SESSION / "session.yaml")
    hal = SimulatedHAL.from_session(session, site_count=1)
    dut = create_dut(session.dut_profile, seed=11, sample_yield=False)
    hal.bind_dut(1, dut)
    hal.site(1).dps.set_voltage("VDD", 3.3)
    hal.site(1).dps.connect("VDD")
    ctx = SiteContext(site_id=1, dut=dut, pin_map=hal.pin_map(1))
    return session, hal, dut, ctx


def _executor(session, hal, nodes, *, events=None, **kwargs) -> FlowExecutor:
    return FlowExecutor(
        program_nodes=nodes,
        stage=kwargs.pop("stage", "CP1"),
        limits=LimitsTable(session.limits),
        binning=BinningEngine(session.binning),
        hal=hal,
        events=events or EventBus(),
        pattern_loader=lambda name: load_pattern(SESSION / "patterns" / name),
        **kwargs,
    )


PASS_FLOW = [
    _node(item="continuity", test_num=1000, params={"pins": ["RSTN", "EN"]},
          on_fail={"action": "stop", "bin": 2}),
    _node(item="idd_static", test_num=2000, on_fail={"action": "continue"}),
]


# ----------------------------------------------------------------- basic flow
def test_flow_runs_every_item_and_bins_the_part(rig) -> None:
    session, hal, dut, ctx = rig
    executor = _executor(session, hal, PASS_FLOW)
    part = executor.run_part(ctx)
    assert [result.test_num for result in part.results] == [1000, 1001, 2000]
    assert part.soft_bin == 1
    assert part.hard_bin == 1
    assert part.passed


def test_limits_from_csv_are_applied(rig) -> None:
    session, hal, dut, ctx = rig
    executor = _executor(session, hal, [PASS_FLOW[1]])
    part = executor.run_part(ctx)
    result = part.results[0]
    assert result.lo_limit == pytest.approx(0.0)
    assert result.hi_limit == pytest.approx(260e-6)


def test_fail_with_stop_action_halts_the_flow(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("open", pin="RSTN"))
    executor = _executor(session, hal, PASS_FLOW)
    part = executor.run_part(ctx)
    assert not part.passed
    assert part.soft_bin == 2
    assert ctx.stopped
    assert all(result.test_num < 2000 for result in part.results)


def test_fail_with_continue_action_keeps_going(rig) -> None:
    session, hal, dut, ctx = rig
    dut.inject(Defect("high_idd", factor=6.0))
    nodes = [
        _node(item="idd_static", test_num=2000, params={"hi": 100e-6},
              on_fail={"action": "continue"}),
        _node(item="delay_ms", test_num=4000, params={"ms": 0.1}),
    ]
    executor = _executor(session, hal, nodes)
    part = executor.run_part(ctx)
    assert not part.passed
    assert part.soft_bin == 4
    assert any(result.test_num == 4000 for result in part.results)


def test_disabled_node_is_skipped(rig) -> None:
    session, hal, dut, ctx = rig
    executor = _executor(session, hal, [
        _node(item="idd_static", test_num=2000, enabled=False),
        _node(item="delay_ms", test_num=4000, params={"ms": 0.1}),
    ])
    part = executor.run_part(ctx)
    assert [result.test_num for result in part.results] == [4000]


def test_groups_are_expanded(rig) -> None:
    session, hal, dut, ctx = rig
    nodes = [_node(group="params", items=[
        _node(item="idd_static", test_num=2000),
        _node(item="delay_ms", test_num=4000, params={"ms": 0.1}),
    ])]
    executor = _executor(session, hal, nodes)
    part = executor.run_part(ctx)
    assert {result.test_num for result in part.results} == {2000, 4000}


def test_only_and_skip_filter_items(rig) -> None:
    session, hal, dut, ctx = rig
    only = _executor(session, hal, PASS_FLOW, only=["idd_static"])
    part = only.run_part(ctx)
    assert [result.test_num for result in part.results] == [2000]

    ctx.new_part(part_id="D2")
    skip = _executor(session, hal, PASS_FLOW, skip=["continuity"])
    part = skip.run_part(ctx)
    assert [result.test_num for result in part.results] == [2000]


def test_empty_flow_still_bins_the_part(rig) -> None:
    session, hal, dut, ctx = rig
    part = _executor(session, hal, []).run_part(ctx)
    assert part.results == []
    assert part.passed


# ------------------------------------------------------------------- branches
def test_branch_executes_the_matching_arm(rig) -> None:
    session, hal, dut, ctx = rig
    nodes = [
        _node(item="idd_static", test_num=2000),
        _node(branch={
            "if": 'value("idd_static") > 0',
            "then": [{"item": "delay_ms", "test_num": 4000, "params": {"ms": 0.1}}],
            "else": [{"item": "delay_ms", "test_num": 4100, "params": {"ms": 0.1}}],
        }),
    ]
    part = _executor(session, hal, nodes).run_part(ctx)
    assert any(result.test_num == 4000 for result in part.results)
    assert not any(result.test_num == 4100 for result in part.results)


def test_branch_else_arm_runs_when_the_condition_is_false(rig) -> None:
    session, hal, dut, ctx = rig
    nodes = [
        _node(item="idd_static", test_num=2000, params={"hi": 1e-9},
              on_fail={"action": "continue"}),
        _node(branch={
            "if": 'value("idd_static") > 1',
            "then": [{"item": "delay_ms", "test_num": 4100, "params": {"ms": 0.1}}],
            "else": [{"item": "delay_ms", "test_num": 4200, "params": {"ms": 0.1}}],
        }),
    ]
    part = _executor(session, hal, nodes).run_part(ctx)
    assert not any(result.test_num == 4100 for result in part.results)
    assert any(result.test_num == 4200 for result in part.results)


def test_loop_repeats_the_body(rig) -> None:
    session, hal, dut, ctx = rig
    nodes = [_node(loop={"count": 3, "items": [
        {"item": "delay_ms", "test_num": 4000, "params": {"ms": 0.1}},
    ]})]
    part = _executor(session, hal, nodes).run_part(ctx)
    assert len([r for r in part.results if r.test_num == 4000]) == 3


def test_goto_bin_forces_the_soft_bin(rig) -> None:
    session, hal, dut, ctx = rig
    nodes = [
        _node(item="idd_static", test_num=2000),
        _node(goto_bin=7),
    ]
    part = _executor(session, hal, nodes).run_part(ctx)
    assert part.soft_bin == 7


# ------------------------------------------------------------- flow utilities
def test_iter_flow_items_flattens_groups_and_loops() -> None:
    nodes = [
        _node(item="delay_ms", test_num=1),
        _node(group="g", items=[_node(item="delay_ms", test_num=2)]),
        _node(loop={"count": 2, "items": [{"item": "delay_ms", "test_num": 3}]}),
    ]
    assert [node.test_num for node in iter_flow_items(nodes)] == [1, 2, 3]


def test_safe_expression_supports_variables_and_comparisons(rig) -> None:
    session, hal, dut, ctx = rig
    ctx.vars["limit"] = 3
    ctx.add_result(Measurement(test_num=2000, test_name="idd_static", site=1, value=1e-4))
    assert SafeExpression('var("limit") > 2').evaluate(ctx) is True
    assert SafeExpression('get("limit") == 3 and var("missing", 0) == 0').evaluate(ctx) is True
    assert SafeExpression('value("idd_static") < 1e-3').evaluate(ctx) is True
    assert SafeExpression('passed("idd_static")').evaluate(ctx) is True
    assert SafeExpression('max(1, 5) == 5').evaluate(ctx) is True
    assert evaluate_branch('var("limit") < 1', ctx) is False


def test_safe_expression_rejects_dangerous_code(rig) -> None:
    session, hal, dut, ctx = rig
    for source in ("__import__('os').system('echo hi')", "open('/etc/passwd')",
                   "[x for x in range(3)]", "lambda: 1"):
        with pytest.raises(ExpressionError):
            SafeExpression(source).evaluate(ctx)


def test_safe_expression_reports_unknown_name(rig) -> None:
    session, hal, dut, ctx = rig
    with pytest.raises(ExpressionError):
        SafeExpression("no_such_variable").evaluate(ctx)


# --------------------------------------------------------------------- events
def test_executor_publishes_item_and_part_events(rig) -> None:
    session, hal, dut, ctx = rig
    events = EventBus()
    seen: list[EventKind] = []
    events.subscribe("*", lambda event: seen.append(event.kind))
    _executor(session, hal, PASS_FLOW[:1], events=events).run_part(ctx)
    assert EventKind.PART_START in seen
    assert EventKind.TEST_RESULT in seen
    assert EventKind.PART_END in seen


def test_result_event_carries_the_measurement(rig) -> None:
    session, hal, dut, ctx = rig
    events = EventBus()
    payloads: list[dict] = []
    events.subscribe(EventKind.TEST_RESULT,
                     lambda event: payloads.append(dict(event.data)))
    _executor(session, hal, PASS_FLOW[1:], events=events).run_part(ctx)
    assert payloads
    assert payloads[0]["result"]["test_num"] == 2000
    assert payloads[0]["result"]["value"] is not None
    assert payloads[0]["site"] == 1


# ------------------------------------------------------------- run control
def test_stop_event_aborts_the_flow(rig) -> None:
    session, hal, dut, ctx = rig
    stop = threading.Event()
    stop.set()
    executor = _executor(session, hal, PASS_FLOW, stop_event=stop)
    with pytest.raises(RunAborted):
        executor.run_part(ctx)


def test_on_item_done_callback_receives_results(rig) -> None:
    session, hal, dut, ctx = rig
    collected: list[int] = []
    executor = _executor(session, hal, PASS_FLOW[1:],
                         on_item_done=lambda _ctx, _item, results:
                         collected.extend(r.test_num for r in results))
    executor.run_part(ctx)
    assert collected == [2000]


def test_contact_fail_is_recorded_as_a_part_issue(rig) -> None:
    session, hal, dut, ctx = rig
    ctx.extra["contact_fail"] = True
    part = _executor(session, hal, PASS_FLOW[1:]).run_part(ctx)
    assert not part.passed
    assert part.soft_bin == 2
