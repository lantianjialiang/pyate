"""Online statistics preserve sample moments and never pool incomparable corners."""
import statistics

import pytest

from pyate.batch.report import merge_capability
from pyate.datalog.result import CpkAccumulator


def test_online_moments_match_direct_sample_statistics():
    acc = CpkAccumulator()
    values = [1e6 + (i % 19) / 100 for i in range(10000)]
    for value in values:
        acc.add(1,value,'IDD',0,2e6,unit='A',stage='CP1',temperature=25,revision='R1')
    result = acc.report()[0]
    assert result.count == len(values)
    assert result.mean == pytest.approx(statistics.mean(values))
    assert result.sigma == pytest.approx(statistics.stdev(values),rel=1e-6)
    assert len(acc._groups) == 1
    # A snapshot must not mutate when more measurements arrive.
    acc.add(1,1,'IDD',0,2e6,unit='A',stage='CP1',temperature=25,revision='R1')
    assert result.count == 10000


@pytest.mark.parametrize('changed', [{'unit':'mA'}, {'stage':'FT1'}, {'temperature':125}, {'revision':'R2'}])
def test_population_metadata_prevents_incompatible_pooling(changed):
    acc = CpkAccumulator()
    meta = {'unit': 'A','stage': 'CP1','temperature': 25,'revision': 'R1'}
    acc.add(1,1,'IDD',0,10,**meta)
    acc.add(1,2,'IDD',0,10,**{**meta, **changed})
    rows = [r.to_dict() for r in acc.report()]
    assert len(rows) == len(merge_capability(rows)) == 2
    assert all(row['cp'] is None and row['cpk'] is None for row in rows)
