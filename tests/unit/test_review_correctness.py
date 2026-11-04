"""Behavioral regressions for review B01/B02/B04/B05.

The byte fixtures and offsets below follow STDF V4, independent of our decoder.
"""
import math
import struct

import pytest

from pyate.config.models import Limit, LimitsConfig
from pyate.config.validators import validate_session
from pyate.datalog.stdf.records import encode_dn, iter_records
from pyate.datalog.stdf.writer import StdfWriter
from pyate.engine.context import PartResult
from pyate.engine.context import TestResult as Result
from pyate.testitems.base import LimitsTable


@pytest.mark.parametrize("value", [0.0, 1.0, math.nan, math.inf, -math.inf])
def test_explicit_failure_cannot_become_pass(value):
    result = Result(1, "search", 1, value=value).fail(7)
    result.judge(-1e30, 1e30)
    assert not result.passed


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_unbounded_measurement_fails(value):
    result = Result(1, "measurement", 1, value=value).judge()
    assert not result.passed
    assert not result.measurement_valid


def test_specific_stage_and_scaled_limits(cp_session):
    rows = [Limit(test_num=1, stage="*", hi=100),
            Limit(test_num=1, stage="CP1", hi=1, scale=-3, temp_min=0, temp_max=50),
            Limit(test_num=1, stage="CP1", hi=2, temp_min=51, temp_max=125)]
    cp_session.limits = LimitsConfig(limits=rows)
    assert validate_session(cp_session).ok
    selected = LimitsTable(cp_session.limits).lookup(1, "CP1", 25)
    assert selected.physical_bounds() == (None, .001)
    assert not Result(1, "idd", 1, value=.002).judge(*selected.physical_bounds()).passed
    cp_session.limits.limits.append(Limit(test_num=1, stage="CP1", temp_min=10, temp_max=40))
    assert any("overlapping" in e for e in validate_session(cp_session).errors)


@pytest.mark.parametrize("order,prefix,cpu", [("little", "<", 2), ("big", ">", 1)])
def test_standard_bytes_flags_and_milliseconds(tmp_path, order, prefix, cpu):
    path = tmp_path / "standard.stdf"
    writer = StdfWriter(path, byte_order=order)
    writer.write_mir(lot_id="oracle")
    part = PartResult("P", 1, soft_bin=2, hard_bin=2, test_time_ms=1234)
    writer.start_part(part)
    writer.write_result(Result(7, "bad", 1, value=9, passed=False))
    writer.end_part(part)
    writer.close()
    raw = path.read_bytes()
    assert raw[:6] == struct.pack(prefix + "HBBBB", 2, 0, 10, cpu, 4)
    offset = 0
    fields = {}
    while offset < len(raw):
        size, typ, sub = struct.unpack_from(prefix + "HBB", raw, offset)
        body = raw[offset + 4:offset + 4 + size]
        fields[typ, sub] = body
        offset += size + 4
    assert fields[15, 10][6] == 0x80  # TEST_FLG bit 7
    assert fields[5, 20][2] == 0x08  # PART_FLG bit 3
    assert struct.unpack_from(prefix + "I", fields[5, 20], 13)[0] == 1234
    assert dict(iter_records(raw))["PRR"]["TEST_T"] == 1234
    assert encode_dn([0, 9], 16, order) == struct.pack(prefix + "H", 16) + b"\x01\x02"
    assert len(encode_dn([256], 257, order)) == 35


def test_inconsistent_cpu_is_rejected_before_open(tmp_path):
    path = tmp_path / "bad.stdf"
    with pytest.raises(ValueError, match="CPU_TYPE"):
        StdfWriter(path, byte_order="big", cpu_type=2)
    assert not path.exists()


def test_stdf_replacement_is_atomic_until_close(tmp_path):
    path = tmp_path / "existing.stdf"
    path.write_bytes(b"previous complete run")
    writer = StdfWriter(path)
    assert path.read_bytes() == b"previous complete run"
    assert path.with_name(path.name + '.partial').exists()
    writer.close()
    assert path.read_bytes()[:6] == b'\x02\x00\x00\x0a\x02\x04'


def test_pattern_control_loop_budget_and_completion():
    from pyate.config.models import DutProfile, LevelsSet, TimingSet
    from pyate.dut.registry import create_dut
    from pyate.pattern.engine import PatternEngine
    from pyate.pattern.parser import parse_pattern_text
    dut = create_dut(DutProfile(model='counter'))
    dut.power(3.3)
    engine = PatternEngine(dut, LevelsSet(name='lv'), TimingSet(name='t'))
    loop = parse_pattern_text('pattern no_vector;\npins CLK;\nlabel start:\njump start;')
    result = engine.run(loop, max_instructions=20)
    assert not result.passed and result.stopped_at == 'instruction_budget'
    vectors = parse_pattern_text('pattern window;\npins CLK;\nrepeat 5 CLK=C;\nhalt;')
    assert not engine.run(vectors,max_cycles=1).passed
    window = engine.run(vectors,max_cycles=1,completion_mode='window')
    assert window.passed and not window.completed and window.status == 'window'


def test_batch_fail_fast_does_not_dispatch_pending_jobs(tmp_path):
    from pyate.batch.runner import BatchRunner
    from pyate.batch.spec import BatchSpec, JobSpec
    jobs = [JobSpec(id=str(i), session=str(tmp_path/'missing.yaml')) for i in range(4)]
    spec = BatchSpec(executor='thread',max_workers=1,fail_fast=True,output_dir=str(tmp_path/'batch'))
    result = BatchRunner(spec,jobs=jobs).run()
    assert [j.status for j in result.jobs] == ['FAILED','ABORTED','ABORTED','ABORTED']
    assert result.counts['RUNNING'] == result.counts['PENDING'] == 0


def test_atomic_replacement_retries_temporary_sharing_lock(tmp_path, monkeypatch):
    import os

    from pyate._files import replace_atomic
    original = os.replace
    source, target = tmp_path/'new', tmp_path/'existing'
    source.write_bytes(b'new')
    target.write_bytes(b'old')
    attempts = []
    def transient(src,dst):
        attempts.append(1)
        if len(attempts) < 3:
            assert target.read_bytes() == b'old'
            raise PermissionError('temporary sharing lock')
        original(src,dst)
    monkeypatch.setattr(os,'replace',transient)
    replace_atomic(source,target)
    assert len(attempts) == 3 and target.read_bytes() == b'new'
