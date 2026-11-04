"""Validation reports independent input errors before touching any DUT."""
from pathlib import Path

import pytest
import yaml

from pyate.config.loader import SECTIONS, ConfigError, load_session, read_config
from pyate.config.models import FlowNode
from pyate.config.validators import validate_session

ROOT = Path(__file__).resolve().parents[2]


def test_collects_multiple_file_schema_errors(tmp_path):
    source = ROOT/'configs/demo_cp/session.yaml'
    data = read_config(source)
    for key in SECTIONS:
        if isinstance(data.get(key), str):
            data[key] = str((source.parent/data[key]).resolve())
    tester = tmp_path/'broken_tester.yaml'
    tester.write_text('unexpected_field: 3\n', encoding='utf-8')
    levels = tmp_path/'broken_levels.yaml'
    levels.write_text('sets: bad\n', encoding='utf-8')
    data.update(tester=str(tester), levels=str(levels), limits=str(tmp_path/'missing.csv'))
    session = tmp_path/'session.yaml'
    session.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ConfigError) as caught:
        load_session(session)
    message = str(caught.value)
    assert all(name in message for name in ['broken_tester.yaml', 'unexpected_field',
                                            'broken_levels.yaml', 'sets', 'missing.csv'])


def test_checks_unreachable_control_nodes_and_item_params():
    cfg = load_session(ROOT/'configs/demo_cp/session.yaml')
    cfg.program.flows['CP1'] = [
        FlowNode(branch={'if': "open('file')", 'then': [{'goto_bin':999}]}),
        FlowNode(loop={'count':-1, 'items':[]}),
        FlowNode(item='vdd_min_search', test_num=4100, params={'resolution':0}),
    ]
    message = '\n'.join(validate_session(cfg).errors)
    assert all(text in message for text in ['branch.if', 'goto_bin', 'loop.count', 'resolution'])
