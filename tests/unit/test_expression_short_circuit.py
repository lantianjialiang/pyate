"""Flow guards must not evaluate unreachable operands."""
import pytest

from pyate.engine.context import SiteContext
from pyate.engine.flow import ExpressionError, SafeExpression


@pytest.mark.parametrize('source, expected', [
    ('True or 1 / 0', True),
    ('False and 1 / 0', False),
    ("var('missing') is None", None),
    ("var('missing', 0) != 0 and 1 / var('missing', 0) > 1", False),
    ('False or True and True', True),
])
def test_boolean_guards_short_circuit(source, expected):
    if expected is None:
        with pytest.raises(ExpressionError):
            SafeExpression(source)
    else:
        assert SafeExpression(source).evaluate(SiteContext(site_id=1)) is expected


def test_reachable_bad_operand_still_reports_error():
    with pytest.raises(ExpressionError, match='division by zero'):
        SafeExpression('False or 1 / 0').evaluate(SiteContext(site_id=1))
