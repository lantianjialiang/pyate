"""Review regressions: physical identity, crash recovery and output failures."""
from pathlib import Path

import pytest

from pyate.config.loader import load_session
from pyate.config.models import DutProfile
from pyate.datalog.factory import build_sinks
from pyate.datalog.stdf.reader_check import verify_stdf
from pyate.dut.registry import create_dut
from pyate.dut.sampling import YieldSampler
from pyate.engine.controller import RunController
from pyate.engine.events import EventBus, EventKind

ROOT = Path(__file__).resolve().parents[2]


def session(sites=2):
    return load_session(ROOT/'configs/demo_ft/session.yaml', {
        'run.sites':sites,'lot.trays':1,'lot.parts_per_tray':8,'handler.contact_fail_prob':0,
        'handler.jam_prob':0,'run.max_retries':0,'run.parallel_mode':'sequential',
        'program.flows.FT1':[{'item':'idd_static','test_num':2000}],
    })


def test_sampler_is_independent_of_previous_draws():
    profile = DutProfile(model='counter')
    sampler = YieldSampler(profile.yield_, 42)
    expected = repr(create_dut(profile, seed=42, x=1,y=2,sampler=sampler).defects)
    for i in range(10):
        create_dut(profile,seed=42,x=i,y=9,sampler=sampler)
    assert repr(create_dut(profile,seed=42,x=1,y=2,sampler=sampler).defects) == expected


def test_part_values_are_identical_across_sites(tmp_path):
    results=[]
    for sites in (1,2,4,8):
        ctl=RunController(session(sites),output_dir=tmp_path/str(sites))
        ctl.run()
        results.append({p.part_id:(p.soft_bin,p.results[0].value) for p in ctl.part_results('T01')})
    assert all(result == results[0] for result in results[1:])


def test_committed_history_can_be_paged_after_controller_restart(tmp_path):
    cfg = session()
    controller = RunController(cfg, output_dir=tmp_path)
    controller.run()
    expected = controller.part_results('T01')
    restarted = RunController(cfg, output_dir=tmp_path)
    page = list(restarted.iter_part_results('T01', offset=3, limit=2))
    assert [part.to_dict() for part in page] == [part.to_dict() for part in expected[3:5]]
    assert list(restarted.iter_part_results('T01', limit=0)) == []
    assert list(restarted.iter_part_results('T01', offset=100)) == []
    assert list(restarted.iter_part_results('unknown')) == []
    with pytest.raises(ValueError):
        list(restarted.iter_part_results('T01', offset=-1))


def test_abort_resume_preserves_all_parts_and_stdf(tmp_path):
    cfg=session()
    bus=EventBus()
    ctl=RunController(cfg,output_dir=tmp_path,events=bus,
                      sinks=build_sinks(cfg,stage='FT1',output_dir=tmp_path))
    bus.subscribe(EventKind.PROGRESS,lambda e:ctl.abort())
    first=ctl.run()
    assert first.aborted and first.status == 'ABORTED'
    assert first.total_tested == 2
    resumed=RunController(cfg,output_dir=tmp_path,resume=True,
                          sinks=build_sinks(cfg,stage='FT1',output_dir=tmp_path))
    summary=resumed.run()
    assert summary.total_tested == 8 and summary.status == 'DONE'
    parts=resumed.part_results('T01')
    assert len({p.part_id for p in parts}) == 8
    assert verify_stdf(next(tmp_path.glob('*.stdf'))).ok
    baseline=RunController(cfg,output_dir=tmp_path/'baseline')
    baseline.run()
    assert [(p.part_id,p.soft_bin,p.results[0].value) for p in parts] == [
        (p.part_id,p.soft_bin,p.results[0].value) for p in baseline.part_results('T01')]
    changed=session()
    changed.run.seed += 1
    with pytest.raises(ValueError,match='mismatch'):
        RunController(changed,output_dir=tmp_path,resume=True).run()


def test_required_sink_failure_is_not_success(tmp_path):
    class Broken:
        def on_part(self,*args):
            raise OSError('disk full')
    ctl=RunController(session(),output_dir=tmp_path,sinks=[Broken()])
    with pytest.raises(OSError,match='required datalog'):
        ctl.run()
    assert ctl.summary.status == 'FAILED'
    assert 'disk full' in ctl.summary.meta['error']


@pytest.mark.parametrize('boundary', ['part', 'checkpoint', 'unit_end'])
def test_crash_boundaries_replay_identical_results(tmp_path, monkeypatch, boundary):
    class CrashSink:
        count = 0

        def on_part(self, *args):
            self.count += 1
            if boundary == 'part' and self.count == 2:
                raise OSError('simulated crash before commit')

        def on_unit_end(self, *args):
            if boundary == 'unit_end':
                raise OSError('simulated crash after unit commit')

    cfg = session()
    interrupted = RunController(cfg, output_dir=tmp_path/'run', sinks=[CrashSink()])
    if boundary == 'checkpoint':
        def broken_checkpoint(*args):
            raise OSError('simulated crash after touchdown commit')
        monkeypatch.setattr(interrupted, '_save_resume_state', broken_checkpoint)
    with pytest.raises(OSError, match='simulated crash'):
        interrupted.run()
    bus = EventBus()
    replayed = []
    bus.subscribe(EventKind.TEST_RESULT, lambda event: replayed.append(event.data))
    resumed = RunController(cfg, output_dir=tmp_path/'run', resume=True, events=bus)
    assert resumed.run().total_tested == 8
    baseline = RunController(cfg, output_dir=tmp_path/'baseline')
    baseline.run()
    def measurements(controller):
        return [(p.part_id, p.soft_bin, [r.value for r in p.results])
                for p in controller.part_results('T01')]
    assert measurements(resumed) == measurements(baseline)
    assert len(replayed) == 8
    assert resumed.population == baseline.population


def test_resume_rejects_effective_options_not_just_session(tmp_path):
    cfg = session()
    RunController(cfg, output_dir=tmp_path).run()
    with pytest.raises(ValueError, match='mismatch'):
        RunController(cfg, site_count=1, output_dir=tmp_path, resume=True).run()


def test_cleanup_preserves_primary_error_and_reports_optional_sink(tmp_path, monkeypatch):
    class BrokenOptional:
        required = False

        def on_part(self, *args):
            raise OSError('optional logger unavailable')

    ctl = RunController(session(), output_dir=tmp_path/'optional', sinks=[BrokenOptional()])
    result = ctl.run()
    assert result.status == 'DONE'
    assert result.meta['optional_sink_errors'][0]['error'] == 'optional logger unavailable'

    class BrokenRequired:
        def on_part(self, *args):
            raise OSError('primary disk error')

    ctl = RunController(session(), output_dir=tmp_path/'required', sinks=[BrokenRequired()])
    def bad_reset():
        raise RuntimeError('secondary cleanup error')
    monkeypatch.setattr(ctl.hal, 'reset', bad_reset)
    with pytest.raises(OSError, match='primary disk error'):
        ctl.run()
    assert ctl.summary.meta['cleanup_errors'] == ['secondary cleanup error']
