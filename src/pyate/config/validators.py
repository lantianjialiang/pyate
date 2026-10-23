"""Cross-file validation.

``validate_session`` never raises on the first problem: it collects every
error (and warning) so a broken config can be fixed in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .loader import LoadedSession
from .models import FlowNode

__all__ = ["ValidationReport", "validate_session", "iter_flow_items", "resolve_pattern_path"]


@dataclass
class ValidationReport:
    """Collected validation errors and warnings."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no error was found."""
        return not self.errors

    def error(self, message: str) -> None:
        """Record an error."""
        self.errors.append(message)

    def warn(self, message: str) -> None:
        """Record a warning."""
        self.warnings.append(message)

    def format(self) -> str:
        """Human readable multi-line summary."""
        lines = [f"ERROR: {msg}" for msg in self.errors]
        lines += [f"WARN:  {msg}" for msg in self.warnings]
        return "\n".join(lines) if lines else "configuration OK"


def iter_flow_items(nodes: list[FlowNode]) -> Iterator[FlowNode]:
    """Yield every ``item`` node, descending into groups/branches/loops."""
    for node in nodes:
        if node.item is not None:
            yield node
        if node.items:
            yield from iter_flow_items(node.items)
        if node.branch is not None:
            yield from iter_flow_items(node.branch.then)
            yield from iter_flow_items(node.branch.else_)
        if node.loop is not None:
            yield from iter_flow_items(node.loop.items)


def resolve_pattern_path(session: LoadedSession, name: str) -> Path | None:
    """Find a ``.pat`` file: absolute, session-relative or in ``patterns/``."""
    candidate = Path(name)
    options = [candidate] if candidate.is_absolute() else [
        session.root / candidate,
        session.root / "patterns" / candidate,
        session.root.parent / "patterns" / candidate,
    ]
    for option in options:
        if option.is_file():
            return option.resolve()
    return None


def _validate_channels(session: LoadedSession, report: ValidationReport) -> None:
    tester = session.tester
    expanded = session.channel_map.expand()
    if not expanded:
        report.error("channel_map: no pins declared")
        return
    site_count = session.site_count()
    if site_count > tester.num_sites_max:
        report.error(
            f"channel_map: {site_count} sites requested but tester.num_sites_max={tester.num_sites_max}"
        )
    power_owner: dict[int, str] = {}
    for site in range(1, site_count + 1):
        used: dict[int, str] = {}
        for pin, (entry, bit) in expanded.items():
            channel = entry.channel_of(site, bit)
            if channel is None:
                report.error(f"channel_map: pin '{pin}' has no channel for site {site}")
                continue
            pool = tester.dps_channels if entry.type == "power" else tester.digital_channels
            kind = "DPS" if entry.type == "power" else "digital"
            if channel < 1 or channel > pool:
                report.error(
                    f"channel_map: pin '{pin}' site {site} channel {channel} outside "
                    f"1..{pool} ({kind})"
                )
            if channel in used:
                report.error(
                    f"channel_map: channel {channel} used twice on site {site} "
                    f"('{used[channel]}' and '{pin}')"
                )
            used[channel] = pin
            if entry.type == "power":
                owner = f"{pin} site {site}"
                previous = power_owner.get(channel)
                if previous is not None:
                    report.error(
                        f"channel_map: DPS channel {channel} used by {previous} and {owner}"
                    )
                power_owner[channel] = owner

    if session.loadboard is not None:
        board = session.loadboard
        if board.site_count != site_count:
            report.warn(
                f"loadboard: site_count={board.site_count} differs from run sites={site_count}"
            )


def _validate_levels_timing(session: LoadedSession, report: ValidationReport) -> None:
    for cfg, label in ((session.levels, "levels"), (session.timing, "timing")):
        names = [item.name for item in cfg.sets]
        if not names:
            report.error(f"{label}: at least one set is required")
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            report.error(f"{label}: duplicate set name(s): {', '.join(sorted(duplicates))}")
        if cfg.default_set and cfg.default_set not in names:
            report.error(f"{label}: default_set '{cfg.default_set}' not defined")
    for lv in session.levels.sets:
        if not (lv.vil < lv.vih):
            report.error(f"levels: '{lv.name}': vil ({lv.vil}) must be < vih ({lv.vih})")
        if not (lv.vol < lv.voh):
            report.error(f"levels: '{lv.name}': vol ({lv.vol}) must be < voh ({lv.voh})")
        if lv.vdd.min >= lv.vdd.max:
            report.error(f"levels: '{lv.name}': vdd.min must be < vdd.max")


def _validate_limits(session: LoadedSession, report: ValidationReport) -> None:
    seen: dict[tuple[int, str], str] = {}
    for limit in session.limits.limits:
        key = (limit.test_num, limit.stage)
        if key in seen:
            report.error(
                f"limits: test_num {limit.test_num} (stage {limit.stage}) defined more than once"
            )
        seen[key] = limit.name
        if limit.lo is not None and limit.hi is not None and limit.lo > limit.hi:
            report.error(f"limits: test_num {limit.test_num}: lo ({limit.lo}) > hi ({limit.hi})")


def _validate_binning(session: LoadedSession, report: ValidationReport) -> None:
    bins = session.binning
    sbins = [item.sbin for item in bins.soft_bins]
    hbins = [item.hbin for item in bins.hard_bins]
    if len(set(sbins)) != len(sbins):
        report.error("binning: duplicate soft bin number")
    if len(set(hbins)) != len(hbins):
        report.error("binning: duplicate hard bin number")
    if bins.pass_bin not in sbins:
        report.error(f"binning: pass_bin {bins.pass_bin} not defined in soft_bins")
    if bins.default_fail_bin not in sbins:
        report.error(f"binning: default_fail_bin {bins.default_fail_bin} not defined in soft_bins")
    for rule in bins.rules:
        if rule.sbin not in sbins:
            report.error(f"binning: rule references undefined soft bin {rule.sbin}")
        if rule.hbin not in hbins:
            report.error(f"binning: rule references undefined hard bin {rule.hbin}")
    for sbin in bins.bin_priority:
        if sbin not in sbins:
            report.error(f"binning: bin_priority references undefined soft bin {sbin}")


def _validate_program(session: LoadedSession, report: ValidationReport) -> None:
    if session.run.stage not in session.program.flows:
        report.error(
            f"program: stage '{session.run.stage}' not defined "
            f"(available: {', '.join(sorted(session.program.flows))})"
        )
        return
    for stage, nodes in session.program.flows.items():
        test_nums: dict[int, str] = {}
        for node in iter_flow_items(nodes):
            key = node.test_num
            assert key is not None
            if key in test_nums:
                report.error(
                    f"program[{stage}]: test_num {key} used twice "
                    f"('{test_nums[key]}' and '{node.item}')"
                )
            test_nums[key] = str(node.item)
            pattern = node.params.get("pattern")
            if pattern:
                path = resolve_pattern_path(session, str(pattern))
                if path is None:
                    report.error(f"program[{stage}]: pattern '{pattern}' not found")
                else:
                    try:
                        from ..pattern.parser import parse_pattern_file

                        header = parse_pattern_file(path).header()
                        declared = set(session.channel_map.expand())
                        unknown = sorted(set(header["pins"]) - declared)
                        if unknown:
                            report.error(
                                f"pattern '{pattern}': pin(s) not declared in channel_map: "
                                f"{', '.join(unknown)}"
                            )
                        tset = header.get("timing")
                        if tset and tset not in {item.name for item in session.timing.sets}:
                            report.error(f"pattern '{pattern}': timing set '{tset}' not defined")
                        lset = header.get("levels")
                        if lset and lset not in {item.name for item in session.levels.sets}:
                            report.error(f"pattern '{pattern}': level set '{lset}' not defined")
                    except Exception as exc:  # pragma: no cover - parser errors surface here
                        report.error(f"pattern '{pattern}': {exc}")


def _validate_dut(session: LoadedSession, report: ValidationReport) -> None:
    try:
        from ..dut.registry import create_dut, is_registered

        if not is_registered(session.dut_profile.model):
            report.error(f"dut_profile: unknown DUT model '{session.dut_profile.model}'")
            return
        dut = create_dut(session.dut_profile, seed=session.run.seed)
    except Exception as exc:  # pragma: no cover - backend errors
        report.error(f"dut_profile: cannot instantiate '{session.dut_profile.model}': {exc}")
        return
    declared = set(session.channel_map.expand())
    unknown = sorted(set(dut.pins) - declared)
    if unknown:
        report.error(f"dut_profile: DUT pin(s) missing from channel_map: {', '.join(unknown)}")


def _validate_wafer(session: LoadedSession, report: ValidationReport) -> None:
    wafer = session.wafer
    if wafer is None:
        return
    if wafer.diameter_mm <= 0:
        report.error("wafer: diameter_mm must be > 0")
    die_x, die_y = wafer.die_size_mm
    if die_x <= 0 or die_y <= 0:
        report.error("wafer: die_size_mm values must be > 0")
    if wafer.edge_exclusion_mm * 2 >= wafer.diameter_mm:
        report.error("wafer: edge_exclusion_mm leaves no usable area")


def _validate_run(session: LoadedSession, report: ValidationReport) -> None:
    if session.run.sites is not None and session.run.sites < 1:
        report.error("run: sites must be >= 1")
    for site in session.run.disabled_sites:
        if site < 1 or site > session.site_count():
            report.error(f"run: disabled site {site} outside 1..{session.site_count()}")
    if session.sim.noise_sigma < 0:
        report.error("tester.simulation: noise_sigma must be >= 0")
    if session.sim.sim_time_scale < 0:
        report.error("tester.simulation: sim_time_scale must be >= 0")
    if session.stdf.cpu_type not in (1, 2):
        report.error("stdf: cpu_type must be 1 (big endian) or 2 (little endian)")


def validate_session(session: LoadedSession) -> ValidationReport:
    """Run every cross-file check and return the aggregated report."""
    report = ValidationReport()
    if not session.levels.sets or not session.timing.sets:
        report.error("levels/timing: at least one set is required")
    _validate_channels(session, report)
    _validate_levels_timing(session, report)
    _validate_limits(session, report)
    _validate_binning(session, report)
    _validate_program(session, report)
    _validate_dut(session, report)
    _validate_wafer(session, report)
    _validate_run(session, report)
    return report


def format_validation_error(session: LoadedSession, report: ValidationReport) -> str:
    """Pretty printer used by the CLI."""
    lines = [f"{session.path}:"]
    lines += [f"  error: {msg}" for msg in report.errors]
    lines += [f"  warn:  {msg}" for msg in report.warnings]
    return "\n".join(lines)
