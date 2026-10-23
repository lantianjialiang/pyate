"""Pydantic v2 schemas for every YAML/CSV configuration file.

The models are intentionally strict (``extra="forbid"``) so typos surface at
load time instead of silently producing a wrong test program.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "VddSpec", "LevelsSet", "LevelsConfig", "EdgeSpec", "TimingSet", "TimingConfig",
    "TesterConfig", "PinChannel", "ChannelMapConfig", "LoadboardConfig",
    "Limit", "LimitsConfig", "SoftBin", "HardBin", "BinRule", "BinningConfig",
    "DefectSpec", "YieldSpec", "ProcessSpec", "DutProfile", "WaferConfig",
    "LotConfig", "ProberConfig", "HandlerConfig", "StdfOptions", "RunSpec",
    "OnFail", "BranchSpec", "LoopSpec", "FlowNode", "TestProgram", "SessionSpec",
    "SimulationSpec",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------- levels
class VddSpec(_Strict):
    """Nominal / minimum / maximum supply voltage."""

    nom: float = 3.3
    min: float = 3.0
    max: float = 3.6

    @model_validator(mode="after")
    def _order(self) -> "VddSpec":
        if not (self.min <= self.nom <= self.max):
            raise ValueError(f"vdd requires min <= nom <= max (got {self.min}/{self.nom}/{self.max})")
        return self


class LevelsSet(_Strict):
    """One DCDC level set (VIH/VIL/VOH/VOL/termination and drive currents)."""

    name: str
    vil: float = -0.3
    vih: float = 2.0
    vol: float = 0.2
    voh: float = 2.8
    vterm: float = 0.0
    iol: float = 4e-3
    ioh: float = -4e-3
    vdd: VddSpec = Field(default_factory=VddSpec)


class LevelsConfig(_Strict):
    """Container for all level sets."""

    default_set: str | None = None
    sets: list[LevelsSet]

    def get(self, name: str | None = None) -> LevelsSet:
        """Return a level set by name (or the default/first one)."""
        wanted = name or self.default_set
        if wanted is None:
            return self.sets[0]
        for item in self.sets:
            if item.name == wanted:
                return item
        raise KeyError(f"level set '{wanted}' not found")


# --------------------------------------------------------------------- timing
class EdgeSpec(_Strict):
    """Edge placement inside one period (nanoseconds)."""

    drive_on: float = 0.0
    data: float = 5.0
    return_: float = Field(default=0.0, alias="return")
    compare: float = 15.0


class TimingSet(_Strict):
    """One timing set: period plus edge placement."""

    name: str
    period_ns: float = 20.0
    edges: EdgeSpec = Field(default_factory=EdgeSpec)

    @field_validator("period_ns")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("period_ns must be > 0")
        return value


class TimingConfig(_Strict):
    """Container for all timing sets."""

    default_set: str | None = None
    sets: list[TimingSet]

    def get(self, name: str | None = None) -> TimingSet:
        """Return a timing set by name (or the default/first one)."""
        wanted = name or self.default_set
        if wanted is None:
            return self.sets[0]
        for item in self.sets:
            if item.name == wanted:
                return item
        raise KeyError(f"timing set '{wanted}' not found")


# --------------------------------------------------------------------- tester
class SimulationSpec(_Strict):
    """Noise / time simulation knobs."""

    noise_sigma: float = 0.002
    sim_time_scale: float = 0.0
    seed: int = 20260101


class TesterConfig(_Strict):
    """Physical tester description."""

    name: str = "SIM-ATE"
    model: str = "PYATE-SIM"
    num_sites_max: int = 8
    digital_channels: int = 256
    dps_channels: int = 16
    pmu_channels: int = 64
    max_vector_rate_mhz: float = 200.0
    vector_depth: int = 1_000_000
    overhead_ms: float = 0.4
    simulation: SimulationSpec = Field(default_factory=SimulationSpec)


class PinChannel(_Strict):
    """Logical pin (optionally a bus) mapped to physical channels per site."""

    pin: str
    type: Literal["in", "out", "io", "power"] = "io"
    width: int = 1
    channel: int | None = None
    sites: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _expand_bus(self) -> "PinChannel":
        width = self.width
        if "[" in self.pin:
            base, _, spec = self.pin.partition("[")
            lo_s, _, hi_s = spec.rstrip("]").partition(":")
            lo = int(lo_s) if lo_s else 0
            hi = int(hi_s) if hi_s else lo
            width = hi - lo + 1
            self.pin = base
            self.width = width
        if width < 1:
            raise ValueError(f"pin '{self.pin}': width must be >= 1")
        return self

    def pins(self) -> list[str]:
        """Expand the pin (bus) into individual pin names."""
        if self.width <= 1:
            return [self.pin]
        return [f"{self.pin}{i}" for i in range(self.width)]

    def channel_of(self, site: int, index: int = 0) -> int | None:
        """Physical channel for ``site`` (1-based) and bus ``index``."""
        if self.sites:
            base = self.sites.get(site)
        else:
            base = self.channel
        return None if base is None else base + index


class ChannelMapConfig(_Strict):
    """Logical pin to physical channel mapping."""

    pins: list[PinChannel]

    def expand(self) -> dict[str, tuple[PinChannel, int]]:
        """Flatten buses into ``{pin_name: (entry, bit_index)}``."""
        out: dict[str, tuple[PinChannel, int]] = {}
        for entry in self.pins:
            for index, name in enumerate(entry.pins()):
                out[name] = (entry, index)
        return out


class LoadboardConfig(_Strict):
    """Load board / socket / probe card description."""

    board_id: str = "LB-001"
    site_count: int = 4
    relay_map: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------- limits
class Limit(_Strict):
    """A limit row keyed by ``test_num`` (optionally per stage/temperature)."""

    test_num: int
    name: str = ""
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    scale: int = 0
    stage: str = "*"
    temp_min: float = -999.0
    temp_max: float = 999.0

    def matches(self, stage: str, temperature: float) -> bool:
        """True when this row applies to ``stage``/``temperature``."""
        stage_ok = self.stage in ("*", "", stage)
        return stage_ok and self.temp_min <= temperature <= self.temp_max


class LimitsConfig(_Strict):
    """All limit rows."""

    limits: list[Limit] = Field(default_factory=list)


class SoftBin(_Strict):
    """Soft bin definition."""

    sbin: int
    name: str = ""
    pass_fail: Literal["P", "F"] = "F"


class HardBin(_Strict):
    """Hard bin definition."""

    hbin: int
    name: str = ""


class BinRule(_Strict):
    """Soft bin to hard bin mapping."""

    sbin: int
    hbin: int


class BinningConfig(_Strict):
    """Soft/hard bin definitions plus mapping rules."""

    pass_bin: int = 1
    default_fail_bin: int = 9
    soft_bins: list[SoftBin] = Field(default_factory=list)
    hard_bins: list[HardBin] = Field(default_factory=list)
    rules: list[BinRule] = Field(default_factory=list)
    bin_priority: list[int] = Field(default_factory=list)
    fail_code_bins: dict[int, int] = Field(default_factory=dict)

    def hard_bin_of(self, sbin: int) -> int:
        """Map a soft bin to its hard bin (identity when unmapped)."""
        for rule in self.rules:
            if rule.sbin == sbin:
                return rule.hbin
        return sbin


# ------------------------------------------------------------------ dut model
class DefectSpec(_Strict):
    """A single injected defect."""

    kind: Literal[
        "stuck_at", "open", "short", "leakage", "high_idd", "slow", "random_bitflip"
    ]
    pin: str | None = None
    pin2: str | None = None
    value: int | None = None
    prob: float = 1.0
    factor: float = 1.0
    nA: float = 100.0


class YieldSpec(_Strict):
    """Statistical yield / defect mix used to sample a DUT population."""

    pass_rate: float = Field(default=0.9, alias="pass")
    defect_mix: dict[str, float] = Field(
        default_factory=lambda: {
            "stuck_at": 0.4, "open": 0.2, "short": 0.15,
            "leakage": 0.1, "high_idd": 0.1, "slow": 0.05,
        }
    )
    edge_fail_boost: float = 1.6
    cluster_prob: float = 0.02


class ProcessSpec(_Strict):
    """Process variation (relative sigma values)."""

    vth_sigma: float = 0.02
    idd_sigma: float = 0.03
    leak_sigma: float = 0.1


class DutProfile(_Strict):
    """Which DUT model to instantiate, its params, defects and yield model."""

    model: str = "composite_cnt8_sr8"
    params: dict[str, Any] = Field(default_factory=dict)
    defects: list[DefectSpec] = Field(default_factory=list)
    yield_: YieldSpec = Field(default_factory=YieldSpec, alias="yield")
    process: ProcessSpec = Field(default_factory=ProcessSpec)


# --------------------------------------------------------------------- wafer
class WaferConfig(_Strict):
    """Wafer geometry for CP scenarios."""

    diameter_mm: float = 300.0
    die_size_mm: tuple[float, float] = (5.0, 5.0)
    edge_exclusion_mm: float = 3.0
    notch: Literal["bottom", "top", "left", "right"] = "bottom"
    center_die: tuple[int, int] = (0, 0)
    skip_list: list[tuple[int, int]] = Field(default_factory=list)


class LotConfig(_Strict):
    """Lot definition and its process flow."""

    lot_id: str = "LOT0001"
    sublot: str = ""
    wafers: list[str] = Field(default_factory=lambda: ["W01"])
    flow: list[str] = Field(default_factory=lambda: ["CP1"])
    trays: int = 0
    parts_per_tray: int = 500


class SiteLayout(_Strict):
    """Handler/prober multi-site layout."""

    cols: int = 2
    rows: int = 2

    @property
    def count(self) -> int:
        """Number of sites described by the layout."""
        return self.cols * self.rows


class ProberConfig(_Strict):
    """Prober simulation parameters."""

    index_time_ms: float = 250.0
    layout: SiteLayout = Field(default_factory=SiteLayout)
    wafer_change_time_s: float = 12.0
    contact_fail_prob: float = 0.0
    temperature: float = 25.0
    scan: Literal["serpentine", "row", "column"] = "serpentine"


class HandlerConfig(_Strict):
    """Handler simulation parameters."""

    layout: SiteLayout = Field(default_factory=lambda: SiteLayout(cols=4, rows=2))
    cycle_time_ms: float = 180.0
    index_time_ms: float = 0.0
    jam_prob: float = 0.0
    contact_fail_prob: float = 0.002
    tray_capacity: int = 500
    temperature: float = 25.0
    jam_recovery_ms: float = 30_000.0


class StdfOptions(_Strict):
    """STDF V4 output options."""

    output_dir: str = "out"
    gzip: bool = False
    byte_order: Literal["little", "big"] = "little"
    cpu_type: int = 2
    write_wcr: bool = False
    write_csv: bool = True
    write_datalog: bool = True
    include_pir_prr: bool = True


# ----------------------------------------------------------------- flow/run
class OnFail(_Strict):
    """What to do when an item fails."""

    action: Literal["stop", "continue", "retest"] = "continue"
    bin: int | None = None
    max_retries: int = 0


class BranchSpec(_Strict):
    """Conditional branch node."""

    if_: str = Field(alias="if")
    then: list["FlowNode"] = Field(default_factory=list)
    else_: list["FlowNode"] = Field(default_factory=list, alias="else")


class LoopSpec(_Strict):
    """Repeat a block ``count`` times."""

    count: int = 1
    items: list["FlowNode"] = Field(default_factory=list)


class FlowNode(_Strict):
    """One node of a test flow (item, group, branch or loop)."""

    item: str | None = None
    test_num: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    on_fail: OnFail | None = None
    enabled: bool = True
    group: str | None = None
    items: list["FlowNode"] = Field(default_factory=list)
    branch: BranchSpec | None = None
    loop: LoopSpec | None = None
    goto_bin: int | None = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> "FlowNode":
        kinds = [self.item is not None, self.group is not None, self.branch is not None,
                 self.loop is not None, self.goto_bin is not None]
        if sum(kinds) != 1:
            raise ValueError("flow node must define exactly one of: item, group, branch, loop, goto_bin")
        if self.item is not None and self.test_num is None:
            raise ValueError(f"item '{self.item}' requires test_num")
        return self

    def kind(self) -> str:
        """Return ``item``/``group``/``branch``/``loop``/``goto_bin``."""
        for name in ("item", "group", "branch", "loop", "goto_bin"):
            if getattr(self, name) is not None:
                return name
        return "unknown"


class TestProgram(_Strict):
    """A test program: named flows built from :class:`FlowNode` trees."""

    program: str = "PROGRAM"
    revision: str = "1.0"
    flows: dict[str, list[FlowNode]]

    def stages(self) -> list[str]:
        """Stage names defined by this program, in file order."""
        return list(self.flows)

    def flow(self, stage: str) -> list[FlowNode]:
        """Return the flow of ``stage``."""
        if stage not in self.flows:
            known = ", ".join(sorted(self.flows))
            raise KeyError(f"stage '{stage}' not defined in program (available: {known})")
        return self.flows[stage]


class RunSpec(_Strict):
    """Per-run knobs (also settable through ``--set``)."""

    stage: str = "CP1"
    sites: int | None = None
    seed: int = 20260101
    temperature: float | None = None
    output_dir: str | None = None
    parallel_mode: Literal["thread", "sequential", "process", "async"] = "thread"
    disabled_sites: list[int] = Field(default_factory=list)
    stop_on_fail: bool = False
    retest_bins: list[int] = Field(default_factory=list)
    max_retries: int = 0
    only: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)


class SessionSpec(_Strict):
    """``session.yaml``: one run, referencing every other config file."""

    name: str = "session"
    description: str = ""
    includes: list[str] = Field(default_factory=list)
    program: str = "testprogram.yaml"
    tester: str = "tester.yaml"
    channel_map: str = "channel_map.yaml"
    loadboard: str | None = None
    levels: str = "levels.yaml"
    timing: str = "timing.yaml"
    limits: str | None = None
    binning: str = "binning.yaml"
    dut_profile: str = "dut_profile.yaml"
    wafer: str | None = None
    lot: str | None = None
    prober: str | None = None
    handler: str | None = None
    stdf: str | None = None
    run: RunSpec = Field(default_factory=RunSpec)


TestProgram.model_rebuild()
BranchSpec.model_rebuild()
LoopSpec.model_rebuild()
FlowNode.model_rebuild()
