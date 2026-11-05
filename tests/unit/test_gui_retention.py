"""Large event streams must not grow pending rows or per-part chart history."""
import statistics
import threading

import pytest

from pyate.api import Event
from pyate.gui.viewmodels.run_vm import RunViewModel


def test_hundred_thousand_results_keep_exact_totals_and_bounded_history():
    vm = RunViewModel(max_rows=50, max_logs=25, max_history_groups=100)
    for index in range(100_000):
        vm.on_event(Event('TEST_RESULT', {'part_id': str(index), 'result': {
            'test_num': 1, 'test_name': 'IDD', 'site': index % 4 + 1,
            'value': 1e6 + index % 19 / 100, 'passed': index % 5 != 0,
            'lo_limit': 0, 'hi_limit': 2e6, 'pin': f'P{index % 250:03d}',
        }}))
        vm.on_event(Event('LOG', {'message': str(index)}))
    assert len(vm.rows) == len(vm.take_rows()) == 50
    assert len(vm.logs) == len(vm.take_logs()) == 25
    assert vm.take_rows() == vm.take_logs() == []
    # 250 pins rotate, so a bucket is evicted before it comes back round:/n    # only the last 100 (wafer, pin) buckets survive
    assert vm.snapshot()['history_groups'] == 100
    assert len(vm._acc) == len(vm._samples) <= 400
    assert len(vm._totals) == 4
    assert vm.snapshot()['history_evictions'] == 99_900
    summary = vm.summary_rows()[0]
    assert summary.count == 100_000
    assert summary.passed == 80_000
    expected = [1e6 + index % 19 / 100 for index in range(100_000)]
    assert summary.mean == pytest.approx(statistics.mean(expected))
    assert summary.stdev == pytest.approx(statistics.stdev(expected), rel=1e-6)
    assert len(vm.summary_rows(group='pin')) == 100
    assert vm.trend(1, pin='P000') == []
    assert len(vm.histogram(1)) <= 10


def test_concurrent_event_ingestion_and_snapshot_reading():
    vm = RunViewModel(max_history_groups=10)
    errors = []

    def ingest():
        try:
            for index in range(10_000):
                vm.add_measurement(test_num=1, name='IDD', site=index % 4 + 1,
                                   value=float(index), passed=True, part=str(index))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=ingest)
    worker.start()
    while worker.is_alive():
        vm.snapshot()
        vm.summary_rows()
        vm.take_rows()
        vm.histogram(1)
    worker.join()
    assert not errors
    assert vm.summary_rows()[0].count == 10_000


def test_capability_is_undefined_without_sample_variance():
    vm = RunViewModel()
    for _ in range(2):
        vm.add_measurement(test_num=1, name='IDD', site=1, value=1,
                           passed=True, lo=0, hi=2)
        assert vm.summary_rows()[0].cpk is None
