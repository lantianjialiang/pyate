"""Retest policy input and categorical verdict selection."""
import pytest

from pyate.batch.spec import RetestPolicy
from pyate.engine.context import PartResult
from pyate.engine.retest import RetestConfig, merge_attempt, should_retest


@pytest.mark.parametrize('kwargs', [{'keep': 'minimum_bin'}, {'max_retries': -1}, {'on': ['typo']}])
def test_invalid_policy_is_rejected_at_both_entry_points(kwargs):
    with pytest.raises(ValueError):
        RetestConfig(**kwargs)
    with pytest.raises(ValueError):
        RetestPolicy(**kwargs)


@pytest.mark.parametrize('keep', ['last', 'best'])
def test_failing_bin_numbers_are_not_ranked(keep):
    old = PartResult('P', 1, soft_bin=2, passed=False)
    new = PartResult('P', 1, soft_bin=9, passed=False)
    merged = merge_attempt(old, new, keep)
    assert merged.soft_bin == 9
    assert merged.extra['previous_attempts'][0].soft_bin == 2
    assert 'previous_attempts' not in old.extra
    assert merged.to_dict()['extra']['previous_attempts'][0]['soft_bin'] == 2


@pytest.mark.parametrize('keep, expected_pass', [('best', True), ('last', False)])
def test_keep_policy_on_pass_then_fail(keep, expected_pass):
    old = PartResult('P', 1, soft_bin=1, passed=True)
    new = PartResult('P', 1, soft_bin=9, passed=False)
    assert merge_attempt(old, new, keep).passed is expected_pass


def test_retest_attempt_limit_and_skipped_part():
    policy = RetestConfig(bins=[2], max_retries=2)
    part = PartResult('P', 1, soft_bin=2, passed=False)
    assert should_retest(part, policy, 1) and should_retest(part, policy, 2)
    assert not should_retest(part, policy, 3)
    part.skipped = True
    assert not should_retest(part, policy, 1)
