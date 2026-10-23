"""``pyate`` command line interface (argparse based, no extra dependency)."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from pathlib import Path
from typing import Any, Sequence

from .. import __version__
from ..api.controller import RunHandle, RunOptions
from ..api.session import ConfigProblem, Session

__all__ = ["main", "build_parser"]

_EXIT_OK = 0
_EXIT_FAIL = 1
_EXIT_CONFIG = 2


# --------------------------------------------------------------------- helpers
def _parse_set(values: Sequence[str] | None) -> dict[str, Any]:
    """Parse ``--set key=value`` items (values are parsed as YAML scalars)."""
    import yaml

    out: dict[str, Any] = {}
    for raw in values or []:
        key, _, value = str(raw).partition("=")
        if not key or not _:
            raise SystemExit(f"--set expects key=value, got '{raw}'")
        try:
            out[key.strip()] = yaml.safe_load(value) if value.strip() else ""
        except yaml.YAMLError:
            out[key.strip()] = value
    return out


def _print_summary(summary: Any) -> None:
    data = summary.to_dict()
    print(f"lot      : {data['lot_id']}  stage {data['stage']}  program {data['program']}")
    print(f"parts    : {data['total_tested']} tested, {data['total_passed']} passed "
          f"({data['yield_pct']} % yield)")
    print(f"bins     : {data['bin_counts']}")
    print(f"uph      : {data['uph']}   duration: {data['duration_s']} s")
    if data.get("meta", {}).get("retests"):
        print(f"retests  : {data['meta']['retests']}")
    files = data.get("files", {})
    for key in ("summary", "snapshot"):
        if key in files:
            print(f"{key:9s}: {files[key]}")


def _units(summary: Any, limit: int = 12) -> None:
    for unit in summary.units[:limit]:
        print(f"  {unit.kind:6s} {unit.unit_id:10s} parts={unit.tested:5d} "
              f"pass={unit.passed:5d} yield={unit.yield_pct:6.2f}% uph={unit.uph:9.1f}")


# -------------------------------------------------------------------- commands
def cmd_config_validate(args: argparse.Namespace) -> int:
    """``pyate config validate``"""
    session = Session(args.session, _parse_set(args.set))
    report = session.validate()
    try:
        info = session.info()
    except ConfigProblem:
        info = None
    if info is not None:
        print(f"session   : {info.name} ({info.path})")
        print(f"stage     : {info.stage}   sites: {info.sites}   temp: {info.temperature}")
        print(f"program   : {info.program}   stages: {', '.join(info.stages)}")
        print(f"dut model : {info.dut_model}")
    print(report.format())
    return _EXIT_OK if report.ok else _EXIT_FAIL


def cmd_run(args: argparse.Namespace) -> int:
    """``pyate run``"""
    options = RunOptions(
        stage=args.stage,
        sites=args.sites,
        seed=args.seed,
        only=list(args.only or []),
        skip=list(args.skip or []),
        output_dir=args.out,
        stdf=not args.no_stdf,
        resume=args.resume,
    )
    session = Session(args.session, _parse_set(args.set))
    try:
        handle = RunHandle(session, options)
        handle.prepare()
    except ConfigProblem as exc:
        print(f"configuration error:\n{exc}", file=sys.stderr)
        return _EXIT_CONFIG
    if not args.quiet:
        info = session.info()
        print(f"running {info.name} stage {options.stage or info.stage} "
              f"with {options.sites or info.sites} site(s)...", flush=True)
    try:
        summary = handle.run()
    except Exception as exc:  # noqa: BLE001 - surface any runtime error to the user
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_FAIL
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_summary(summary)
        _units(summary, limit=args.units)
    return _EXIT_OK if not summary.aborted else _EXIT_FAIL


def cmd_lot_run(args: argparse.Namespace) -> int:
    """``pyate lot run``: walk the stage flow of a lot with KGD carry-over."""
    from ..api.lot import LotRunner
    from ..api.session import Session

    overrides = _parse_set(args.set)
    sessions: dict[str, Session] = {}
    for spec in args.stage_session or []:
        stage, _, path = spec.partition("=")
        if not path:
            print(f"--stage-session needs STAGE=PATH (got '{spec}')", file=sys.stderr)
            return _EXIT_CONFIG
        sessions[stage.strip().upper()] = Session(path.strip(), overrides)
    stages = [chunk.strip().upper() for chunk in (args.stages or "").split(",") if chunk.strip()]
    runner = LotRunner(Session(args.session, overrides), stages=stages or None,
                       sessions=sessions, output_dir=args.out, stdf=not args.no_stdf)
    try:
        report = runner.run()
    except ConfigProblem as exc:
        print(f"configuration error://n{exc}", file=sys.stderr)
        return _EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 - surface runtime problems to the user
        print(f"lot failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_FAIL
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        for stage in report.stages:
            kinds = {unit.kind for unit in stage.summary.units}
            # only wafer stages carry a die level KGD set
            tail = (f"KGD={len(stage.kgd):5d}" if "wafer" in kinds
                    else f"good={stage.passed:5d}")
            print(f"  {stage.stage:6s} tested={stage.tested:5d} passed={stage.passed:5d} "
                  f"yield={stage.yield_pct:6.2f}%  {tail}")
        print(f"lot {report.lot_id}: first pass {report.first_pass_yield:.2f}%  "
              f"final {report.final_yield:.2f}%  cumulative {report.cumulative_yield():.2f}%")
        print(f"report: {report.output_dir / 'lot_report.md'}")
    return _EXIT_OK


def cmd_batch_run(args: argparse.Namespace) -> int:
    """``pyate batch run``"""
    from ..batch import BatchRunner, expand_jobs, load_batch

    try:
        spec, jobs = load_batch(args.batch)
    except Exception as exc:  # noqa: BLE001 - config error
        print(f"batch error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG
    if args.workers:
        spec = spec.model_copy(update={"max_workers": args.workers})
    if args.fail_fast:
        spec = spec.model_copy(update={"fail_fast": True})
    if args.executor:
        spec = spec.model_copy(update={"executor": args.executor})
    if args.job:
        wanted = set(args.job)
        jobs = [job for job in jobs if job.id in wanted]
        if not jobs:
            print(f"no job matched {sorted(wanted)}", file=sys.stderr)
            return _EXIT_CONFIG
    if not args.quiet:
        print(f"batch {spec.batch_id}: {len(jobs)} job(s), {spec.max_workers} worker(s), "
              f"executor {spec.executor}", flush=True)
    runner = BatchRunner(spec, jobs or expand_jobs(spec), output_dir=args.out)
    summary = runner.run()
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        for job in summary.jobs:
            print(f"  {job.job_id:22s} {job.status:8s} parts={job.tested:5d} "
                  f"yield={job.yield_pct:6.2f}% stdf={len(job.stdf_files)}"
                  + (f"  ERROR {job.error}" if job.error else ""))
        agg = summary.to_dict()["aggregate"]
        print(f"done    : {summary.counts}   duration {summary.duration_s:.1f} s")
        print(f"aggregate: yield {agg['yield_pct']}%  uph {agg['uph_avg']}  "
              f"stdf files {agg['stdf_files']}")
        print(f"report  : {Path(summary.output_dir) / 'batch_report.md'}")
    return _EXIT_OK if not summary.failed else _EXIT_FAIL


def _load_batch_summary(path: str) -> dict[str, Any] | None:
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "batch_summary.json"
    if candidate.suffix in (".yaml", ".yml"):
        from ..batch import load_batch

        spec, _jobs = load_batch(candidate)
        out = Path(spec.output_dir)
        if not out.is_absolute():
            out = spec.root / out
        candidate = out / "batch_summary.json"
    if not candidate.is_file():
        return None
    return json.loads(candidate.read_text(encoding="utf-8"))


def cmd_batch_status(args: argparse.Namespace) -> int:
    """``pyate batch status``"""
    data = _load_batch_summary(args.batch)
    if data is None:
        print(f"no batch summary found for {args.batch}", file=sys.stderr)
        return _EXIT_FAIL
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return _EXIT_OK
    print(f"batch {data['batch_id']}  counts {data['counts']}  "
          f"duration {data['duration_s']} s")
    for job in data["jobs"]:
        print(f"  {job['job_id']:22s} {job['status']:8s} parts={job.get('tested', 0):5d} "
              f"yield={job.get('yield_pct', 0):6.2f}%")
    return _EXIT_OK


def cmd_batch_report(args: argparse.Namespace) -> int:
    """``pyate batch report``"""
    data = _load_batch_summary(args.batch)
    if data is None:
        print(f"no batch summary found for {args.batch}", file=sys.stderr)
        return _EXIT_FAIL
    agg = data.get("aggregate", {})
    if args.json:
        print(json.dumps(agg, indent=2, ensure_ascii=False))
        return _EXIT_OK
    print(f"batch    : {data['batch_id']}")
    print(f"jobs     : {agg.get('jobs')} ({agg.get('done')} done, {agg.get('failed')} failed)")
    print(f"yield    : {agg.get('yield_pct')} % ({agg.get('passed')}/{agg.get('tested')})")
    print(f"uph avg  : {agg.get('uph_avg')}")
    print("bin pareto:")
    for row in agg.get("bin_pareto", []):
        print(f"  bin {row['bin']:3d}  {row['count']:6d}  {row['pct']:6.2f} %")
    caps = [row for row in agg.get("capability", []) if row.get("count")]
    if caps:
        print("capability:")
        for row in caps[: args.cpk]:
            print(f"  {row['test_num']:6d} {row.get('name', ''):24s} n={row['count']:5d} "
                  f"cpk={row['cpk']:7.3f} cp={row['cp']:7.3f}")
    return _EXIT_OK


def cmd_pattern_run(args: argparse.Namespace) -> int:
    """``pyate pattern run``: execute a pattern against a DUT model."""
    from ..config.models import LevelsSet, TimingSet, VddSpec
    from ..dut.registry import create_dut, is_registered
    from ..pattern.engine import PatternEngine
    from ..pattern.parser import load_pattern

    if not is_registered(args.dut):
        from ..dut.registry import list_models

        print(f"unknown DUT model '{args.dut}' (available: {', '.join(list_models())})",
              file=sys.stderr)
        return _EXIT_CONFIG
    pattern = load_pattern(args.pattern)
    profile = _profile_for(args.dut, args.params or {})
    dut = create_dut(profile, seed=args.seed, sample_yield=False)
    unknown = [name for name in pattern.pins if not dut.has_pin(name)]
    if unknown:
        print(f"DUT model '{args.dut}' has no pin(s) {', '.join(unknown)} required by "
              f"{pattern.name}.pat", file=sys.stderr)
        return _EXIT_CONFIG
    levels = LevelsSet(name=pattern.levels or "lv",
                       vdd=VddSpec(nom=args.vdd, min=args.vdd * 0.9, max=args.vdd * 1.1))
    dut.power(args.vdd)
    timing = TimingSet(name=pattern.timing or "t", period_ns=args.period_ns)
    engine = PatternEngine(dut, levels, timing, max_cycles=args.cycles)
    result = engine.run(pattern, start_label=args.start, max_cycles=args.cycles,
                        stop_on_first_fail=not args.all_failures)
    if args.json:
        print(json.dumps({
            "pattern": pattern.name,
            "passed": result.passed,
            "cycles": result.cycles,
            "fail_count": result.fail_count,
            "fail_pins": list(result.fail_pins),
            "first_fail": None if not result.fail_log else {
                "cycle": result.fail_log[0].cycle,
                "line": result.fail_log[0].line,
                "pins": list(result.fail_log[0].pins),
                "expected": result.fail_log[0].expected,
                "actual": result.fail_log[0].actual,
            },
            "elapsed_ms": round(result.elapsed_ms, 3),
        }, indent=2))
    else:
        print(f"pattern  : {pattern.name}  ({pattern.vector_count} vectors)")
        print(f"cycles   : {result.cycles}  elapsed {result.elapsed_ms:.2f} ms")
        print(f"result   : {'PASS' if result.passed else 'FAIL'}"
              + ("" if result.passed else f"  ({result.fail_count} fail cycle(s))"))
        if result.fail_log:
            first = result.fail_log[0]
            print(f"first fail: cycle {first.cycle} line {first.line} "
                  f"pins={','.join(first.pins)}")
    return _EXIT_OK if result.passed else _EXIT_FAIL


def _profile_for(model: str, params: dict[str, Any]) -> Any:
    """Build a ``DutProfile`` for a standalone pattern run."""
    from ..config.models import DutProfile, YieldSpec

    return DutProfile.model_validate({
        "model": model,
        "params": params,
        "defects": [],
        "yield": YieldSpec.model_validate({"pass": 1.0}).model_dump(by_alias=True),
    })


def cmd_stdf_verify(args: argparse.Namespace) -> int:
    """``pyate stdf verify``"""
    from ..datalog.stdf.reader_check import verify_stdf

    report = verify_stdf(args.file, use_pystdf=not args.no_pystdf)
    if args.json:
        print(json.dumps({
            "path": report.path, "ok": report.ok, "records": report.records,
            "parts": report.parts, "ptr": report.ptr_count, "ftr": report.ftr_count,
            "hard_bins": {str(k): v for k, v in report.hard_bins.items()},
            "soft_bins": {str(k): v for k, v in report.soft_bins.items()},
            "pystdf_ok": report.pystdf_ok, "pystdf_error": report.pystdf_error,
            "errors": report.errors, "warnings": report.warnings,
        }, indent=2))
    else:
        print(report.format())
    return _EXIT_OK if report.ok else _EXIT_FAIL


def _jsonable(value: Any) -> Any:
    """Make decoded STDF field values JSON serialisable (``bytes`` -> hex)."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def cmd_stdf_dump(args: argparse.Namespace) -> int:
    """``pyate stdf dump``"""
    from ..datalog.stdf.reader_check import read_stdf

    records = read_stdf(args.file)
    wanted = {name.upper() for name in (args.type or [])}
    shown = 0
    for name, values in records:
        if wanted and name not in wanted:
            continue
        if args.json:
            print(json.dumps({"record": name, "fields": _jsonable(values)}, ensure_ascii=False))
        else:
            trimmed = {k: v for k, v in values.items() if v not in (None, "", [])}
            print(f"{name}: {json.dumps(_jsonable(trimmed), ensure_ascii=False)}")
        shown += 1
        if args.limit and shown >= args.limit:
            break
    if not args.json:
        print(f"-- {shown} record(s) shown of {len(records)}")
    return _EXIT_OK


def cmd_gui(args: argparse.Namespace) -> int:
    """``pyate gui``"""
    try:
        from ..gui.app import main as gui_main
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        print(f"GUI dependencies are missing ({exc}).\n"
              "Install them with:  pip install 'pyate[gui]'", file=sys.stderr)
        return _EXIT_FAIL
    return int(gui_main(args.session, theme=args.theme) or 0)


def cmd_plugin_new(args: argparse.Namespace) -> int:
    """``pyate plugin new``: scaffold a plugin package."""
    from ..plugins.scaffold import create_plugin

    try:
        path = create_plugin(args.name, directory=args.dir, force=args.force)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return _EXIT_FAIL
    print(f"created plugin skeleton at {path}")
    print("next steps:")
    print(f"  cd {path.parent}")
    print(f"  pip install -e {path.name}")
    return _EXIT_OK


def cmd_version(_args: argparse.Namespace) -> int:
    """``pyate version``"""
    print(f"pyate {__version__}")
    return _EXIT_OK


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    """Build the top level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pyate",
        description="ATE simulator: virtual instruments, DUT models, test flows and STDF output",
    )
    parser.add_argument("--version", action="version", version=f"pyate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config", help="configuration tools")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="validate a session.yaml")
    validate.add_argument("session")
    validate.add_argument("--set", action="append", metavar="K=V")
    validate.set_defaults(func=cmd_config_validate)

    run = sub.add_parser("run", help="run one session")
    run.add_argument("session")
    run.add_argument("--set", action="append", metavar="K=V", help="config override")
    run.add_argument("--sites", type=int, help="number of active sites")
    run.add_argument("--seed", type=int)
    run.add_argument("--stage", help="stage name (CP1/CP2/FT1 ...)")
    run.add_argument("--only", action="append", help="only run these test items")
    run.add_argument("--skip", action="append", help="skip these test items")
    run.add_argument("--out", help="output directory")
    run.add_argument("--no-stdf", action="store_true", help="do not write STDF")
    run.add_argument("--resume", action="store_true", help="resume an interrupted run")
    run.add_argument("--units", type=int, default=12, help="units to print")
    run.add_argument("--json", action="store_true")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    lot = sub.add_parser("lot", help="lot flow across stages (CP1 -> CP2 -> FT1)")
    lot_sub = lot.add_subparsers(dest="lot_command", required=True)
    l_run = lot_sub.add_parser("run", help="run every stage of a lot")
    l_run.add_argument("session", help="session.yaml of the first stage")
    l_run.add_argument("--set", action="append", metavar="K=V", help="config override")
    l_run.add_argument("--stages", help="comma separated stage list (default: lot.flow)")
    l_run.add_argument("--stage-session", action="append", metavar="STAGE=PATH",
                       help="session.yaml to use for one stage (e.g. FT1=.../session.yaml)")
    l_run.add_argument("--out", help="output directory")
    l_run.add_argument("--no-stdf", action="store_true", help="do not write STDF")
    l_run.add_argument("--json", action="store_true")
    l_run.set_defaults(func=cmd_lot_run)

    batch = sub.add_parser("batch", help="batch execution")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    b_run = batch_sub.add_parser("run", help="run a batch.yaml")
    b_run.add_argument("batch")
    b_run.add_argument("--workers", type=int, help="override max_workers")
    b_run.add_argument("--executor", choices=["thread", "process", "serial"])
    b_run.add_argument("--fail-fast", action="store_true")
    b_run.add_argument("--job", action="append", help="run only these job ids")
    b_run.add_argument("--out")
    b_run.add_argument("--json", action="store_true")
    b_run.add_argument("--quiet", action="store_true")
    b_run.set_defaults(func=cmd_batch_run)
    b_status = batch_sub.add_parser("status", help="show job states of a batch")
    b_status.add_argument("batch", help="batch.yaml, output dir or batch_summary.json")
    b_status.add_argument("--json", action="store_true")
    b_status.set_defaults(func=cmd_batch_status)
    b_report = batch_sub.add_parser("report", help="print aggregate report")
    b_report.add_argument("batch")
    b_report.add_argument("--cpk", type=int, default=15, help="capability rows to show")
    b_report.add_argument("--json", action="store_true")
    b_report.set_defaults(func=cmd_batch_report)

    pattern = sub.add_parser("pattern", help="pattern tools")
    pattern_sub = pattern.add_subparsers(dest="pattern_command", required=True)
    p_run = pattern_sub.add_parser("run", help="run a .pat against a DUT model")
    p_run.add_argument("pattern")
    p_run.add_argument("--dut", required=True, help="DUT model name")
    p_run.add_argument("--param", dest="params", action="append", metavar="K=V",
                       help="DUT model parameter (repeatable)")
    p_run.add_argument("--cycles", type=int, default=100_000)
    p_run.add_argument("--period-ns", type=float, default=20.0)
    p_run.add_argument("--vdd", type=float, default=3.3)
    p_run.add_argument("--seed", type=int, default=0)
    p_run.add_argument("--start", help="start label")
    p_run.add_argument("--all-failures", action="store_true", help="do not stop at first fail")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_pattern_run)

    stdf = sub.add_parser("stdf", help="STDF tools")
    stdf_sub = stdf.add_subparsers(dest="stdf_command", required=True)
    s_verify = stdf_sub.add_parser("verify", help="verify an STDF file")
    s_verify.add_argument("file")
    s_verify.add_argument("--no-pystdf", action="store_true")
    s_verify.add_argument("--json", action="store_true")
    s_verify.set_defaults(func=cmd_stdf_verify)
    s_dump = stdf_sub.add_parser("dump", help="dump records")
    s_dump.add_argument("file")
    s_dump.add_argument("--type", action="append", help="record type filter (repeatable)")
    s_dump.add_argument("--limit", type=int, default=20)
    s_dump.add_argument("--json", action="store_true")
    s_dump.set_defaults(func=cmd_stdf_dump)

    gui = sub.add_parser("gui", help="launch the PySide6 GUI")
    gui.add_argument("session", nargs="?")
    gui.add_argument("--theme", choices=["dark", "light"], default="light")
    gui.set_defaults(func=cmd_gui)

    plugin = sub.add_parser("plugin", help="plugin tools")
    plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True)
    p_new = plugin_sub.add_parser("new", help="scaffold a plugin package")
    p_new.add_argument("name")
    p_new.add_argument("--dir", help="parent directory")
    p_new.add_argument("--force", action="store_true")
    p_new.set_defaults(func=cmd_plugin_new)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (console script ``pyate``)."""
    # Required so a frozen ``pyate.exe`` can use batch ``--executor process``.
    multiprocessing.freeze_support()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "pattern" and args.pattern_command == "run":
        args.params = _parse_set(args.params)
    if args.command not in ("plugin", "version"):
        _load_plugins()
    return int(args.func(args) or 0)


def _load_plugins() -> Any:
    """Load installed plugins so their test items / DUT models are available."""
    try:
        from ..plugins import get_manager

        return get_manager()
    except Exception:  # noqa: BLE001 - a broken plugin must not break the CLI
        import logging

        logging.getLogger("pyate.cli").warning("plugin loading failed", exc_info=True)
        return None


if __name__ == "__main__":  # pragma: no cover
    multiprocessing.freeze_support()
    raise SystemExit(main())
