"""Default ``SimulatedBackend`` implementation of the HAL.

All readings come from the DUT behaviour model, optionally decorated with
Gaussian noise, so a fixed seed always reproduces the same numbers.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Iterable, Mapping

from ..config.models import ChannelMapConfig, LevelsConfig, LevelsSet, TimingConfig, TimingSet
from ..dut.base import DUTModel
from .base import HALError, HALBackend, NotConnected, OverCurrent, ResourceConflict, SiteResources
from .digital import DigitalChannel
from .dps import DPS
from .pmu import PMU
from .relay import Relay

__all__ = [
    "SimulatedDPS", "SimulatedPMU", "SimulatedRelay", "SimulatedDigitalChannel",
    "SimulatedHAL",
]

_POWER_PINS = ("VDD", "VCC", "VDDIO", "VDD_CORE", "AVDD")


class _Instrument:
    """Common plumbing: DUT access, noise, simulated time accounting."""

    def __init__(self, hal: "SimulatedHAL", site_id: int) -> None:
        self.hal = hal
        self.site_id = site_id

    def _dut(self) -> DUTModel:
        dut = self.hal.dut(self.site_id)
        if dut is None:
            raise HALError("no DUT bound to this site", site=self.site_id)
        return dut

    def _noise(self, value: float, sigma: float | None = None) -> float:
        return self.hal.apply_noise(self.site_id, value, sigma)

    def _time(self, milliseconds: float) -> None:
        self.hal.add_time(self.site_id, milliseconds)


class SimulatedDPS(_Instrument, DPS):
    """Voltage source with current measurement and compliance."""

    def __init__(self, hal: "SimulatedHAL", site_id: int,
                 limits: Mapping[str, float] | None = None) -> None:
        super().__init__(hal, site_id)
        self._voltage: dict[str, float] = {}
        self._limit: dict[str, float] = dict(limits or {})
        self._connected: set[str] = set()

    def set_voltage(self, pin: str, volts: float) -> None:
        self._voltage[pin] = float(volts)
        self._time(0.05)
        if pin in self._connected:
            self._apply_supply(pin)

    def set_current_limit(self, pin: str, ampere: float) -> None:
        if ampere <= 0:
            raise HALError(f"current limit must be > 0 (got {ampere})", site=self.site_id, pin=pin)
        self._limit[pin] = float(ampere)
        self._time(0.02)

    def connect(self, pin: str) -> None:
        self._connected.add(pin)
        self._voltage.setdefault(pin, 0.0)
        self._time(0.2)
        self._apply_supply(pin)

    def disconnect(self, pin: str) -> None:
        self._connected.discard(pin)
        self._time(0.15)
        if pin in _POWER_PINS or pin.upper().startswith("VDD"):
            self._dut().power(0.0)

    def _apply_supply(self, pin: str) -> None:
        if pin in _POWER_PINS or pin.upper().startswith("VDD") or pin.upper() == "VCC":
            self._dut().power(self._voltage.get(pin, 0.0))

    def measure_current(self, pin: str) -> float:
        if pin not in self._connected:
            raise NotConnected("DPS is not connected", site=self.site_id, pin=pin)
        dut = self._dut()
        volts = self._voltage.get(pin, 0.0)
        current = abs(dut.supply_current(volts))
        measured = abs(self._noise(current, 0.005))
        limit = self._limit.get(pin, 0.1)
        if measured > limit:
            raise OverCurrent(
                f"measured {measured * 1e3:.3f} mA above limit {limit * 1e3:.3f} mA",
                site=self.site_id, pin=pin,
            )
        self._time(0.3)
        return measured

    def measure_voltage(self, pin: str) -> float:
        if pin not in self._connected:
            raise NotConnected("DPS is not connected", site=self.site_id, pin=pin)
        self._time(0.25)
        volts = self._voltage.get(pin, 0.0)
        drop = 0.002 * (abs(self._dut().supply_current(volts)) / 0.1)
        return self._noise(volts - drop, 0.001)

    def is_connected(self, pin: str) -> bool:
        return pin in self._connected

    def status(self) -> dict[str, dict[str, float | bool]]:
        """Debug snapshot of every programmed supply."""
        return {
            pin: {
                "voltage": self._voltage.get(pin, 0.0),
                "limit": self._limit.get(pin, 0.1),
                "connected": pin in self._connected,
            }
            for pin in set(self._voltage) | self._connected
        }


class SimulatedPMU(_Instrument, PMU):
    """FVMI / FIMV parametric unit routed through the relay matrix."""

    def __init__(self, hal: "SimulatedHAL", site_id: int, relay: SimulatedRelay) -> None:
        super().__init__(hal, site_id)
        self._relay = relay
        self._mode: dict[str, str] = {}
        self._force_v: dict[str, float] = {}
        self._force_i: dict[str, float] = {}
        self._connected: set[str] = set()

    @staticmethod
    def _relay_name(pin: str) -> str:
        return f"pmu:{pin}"

    def connect(self, pin: str) -> None:
        self._connected.add(pin)
        self._relay.close(self._relay_name(pin))
        self._time(0.1)

    def disconnect(self, pin: str) -> None:
        self._connected.discard(pin)
        self._mode.pop(pin, None)
        self._relay.open(self._relay_name(pin))
        self._time(0.08)

    def force_voltage(self, pin: str, volts: float, irange: float = 1e-3) -> None:
        if irange <= 0:
            raise HALError("irange must be > 0", site=self.site_id, pin=pin)
        self.connect(pin)
        self._mode[pin] = "fvmi"
        self._force_v[pin] = float(volts)
        self._time(0.15)

    def force_current(self, pin: str, ampere: float, vclamp: float = 5.0) -> None:
        if abs(ampere) > 0.05:
            raise OverCurrent(
                f"forced current {ampere * 1e3:.1f} mA exceeds PMU range",
                site=self.site_id, pin=pin,
            )
        self.connect(pin)
        self._mode[pin] = "fimv"
        self._force_i[pin] = float(ampere)
        self._time(0.15)

    def _require(self, pin: str) -> str:
        if pin not in self._connected:
            raise NotConnected("PMU is not connected to this pin", site=self.site_id, pin=pin)
        return self._mode.get(pin, "fvmi")

    def measure_current(self, pin: str) -> float:
        mode = self._require(pin)
        self._time(0.25)
        if mode == "fimv":
            return self._noise(self._force_i.get(pin, 0.0), 0.01)
        dut = self._dut()
        volts = self._force_v.get(pin, 0.0)
        return self._noise(dut.pin_leakage(pin, volts), 0.02)

    def measure_voltage(self, pin: str) -> float:
        mode = self._require(pin)
        self._time(0.25)
        if mode == "fimv":
            dut = self._dut()
            polarity = 1 if self._force_i.get(pin, 0.0) >= 0 else -1
            return self._noise(dut.continuity_vdiode(pin, polarity), 0.004)
        return self._noise(self._force_v.get(pin, 0.0), 0.002)

    def is_connected(self, pin: str) -> bool:
        return pin in self._connected


class SimulatedRelay(Relay):
    """Named relay matrix with open/close bookkeeping."""

    def __init__(self, hal: "SimulatedHAL", site_id: int) -> None:
        self.hal = hal
        self.site_id = site_id
        self._state: dict[str, bool] = {}

    def close(self, name: str) -> None:
        self._state[name] = True
        self.hal.add_time(self.site_id, 0.05)

    def open(self, name: str) -> None:
        self._state[name] = False
        self.hal.add_time(self.site_id, 0.05)

    def is_closed(self, name: str) -> bool:
        return bool(self._state.get(name, False))

    def open_all(self) -> None:
        for name in list(self._state):
            self._state[name] = False

    def closed_relays(self) -> list[str]:
        """Every currently closed relay (debug)."""
        return sorted(name for name, closed in self._state.items() if closed)


class SimulatedDigitalChannel(_Instrument, DigitalChannel):
    """Pin electronics: level/timing sets plus pattern execution."""

    def __init__(self, hal: "SimulatedHAL", site_id: int, *,
                 levels: LevelsSet, timing: TimingSet,
                 timing_map: dict[str, TimingSet] | None = None) -> None:
        super().__init__(hal, site_id)
        self.dut: DUTModel | None = None
        self.levels = levels
        self.timing = timing
        self.timing_map: dict[str, TimingSet] = dict(timing_map or {})
        self.pattern = None
        self.engine = None

    def apply_levels(self, levels: LevelsSet) -> None:
        self.levels = levels

    def apply_timing(self, timing: TimingSet) -> None:
        self.timing = timing

    def override_timings(self, timing: TimingSet) -> None:
        """Force every referenced timing set to ``timing`` (fmax/shmoo search)."""
        self.timing = timing
        if self.timing_map:
            self.timing_map = {name: timing for name in self.timing_map}
        else:
            self.timing_map = {timing.name: timing}

    def load_pattern(self, pattern: Any) -> Any:
        from ..pattern.parser import load_pattern

        self.pattern = load_pattern(pattern)
        return self.pattern

    def set_vdd(self, volts: float) -> None:
        if self.dut is None:
            raise HALError("no DUT bound", site=self.site_id)
        self.dut.power(volts)
        self.hal.add_time(self.site_id, 0.2)

    def run(self, *, start_label: str | None = None, stop_on_first_fail: bool = True,
            max_cycles: int | None = None, capture_start: int | None = None,
            capture_stop: int | None = None) -> Any:
        from ..pattern.engine import PatternEngine

        if self.dut is None:
            raise HALError("no DUT bound", site=self.site_id)
        if self.pattern is None:
            raise HALError("no pattern loaded", site=self.site_id)
        engine = PatternEngine(
            self.dut,
            levels=self.levels,
            timing=self.timing,
            timings=self.timing_map,
            rng=self.hal.rng_for(self.site_id),
            noise_sigma=self.hal.noise_sigma,
            max_fail_log=self.hal.max_fail_log,
        )
        result = engine.run(
            self.pattern,
            start_label=start_label,
            stop_on_first_fail=stop_on_first_fail,
            max_cycles=max_cycles,
            capture_start=capture_start,
            capture_stop=capture_stop,
        )
        self.hal.add_time(self.site_id, result.elapsed_ms)
        return result


class SimulatedHAL(HALBackend):
    """Multi-site simulated tester built from the session configuration."""

    def __init__(
        self,
        *,
        site_count: int,
        channel_map: ChannelMapConfig,
        levels: LevelsConfig,
        timing: TimingConfig,
        seed: int = 0,
        noise_sigma: float = 0.002,
        sim_time_scale: float = 0.0,
        max_fail_log: int = 1000,
        default_levels: str | None = None,
        default_timing: str | None = None,
    ) -> None:
        self.site_count = int(site_count)
        self.channel_map = channel_map
        self.level_config = levels
        self.timing_config = timing
        self.noise_sigma = float(noise_sigma)
        self.sim_time_scale = float(sim_time_scale)
        self.max_fail_log = int(max_fail_log)
        self.seed = int(seed)
        self._lock = threading.RLock()
        self._sites: dict[int, SiteResources] = {}
        self._duts: dict[int, DUTModel] = {}
        self._rngs: dict[int, random.Random] = {}
        self._time_ms: dict[int, float] = {}
        self._pin_maps: dict[int, dict[str, int]] = {}
        self._build(levels.get(default_levels), timing.get(default_timing))

    # ---------------------------------------------------------------- build
    def _build(self, levels: LevelsSet, timing: TimingSet) -> None:
        expanded = self.channel_map.expand()
        for site in range(1, self.site_count + 1):
            pin_map: dict[str, int] = {}
            for pin, (entry, bit) in expanded.items():
                channel = entry.channel_of(site, bit)
                if channel is not None:
                    pin_map[pin] = channel
            duplicates = len(set(pin_map.values())) != len(pin_map)
            if duplicates:
                raise ResourceConflict(f"duplicate channel assignment on site {site}", site=site)
            self._pin_maps[site] = pin_map
            self._rngs[site] = random.Random(self.seed * 7919 + site * 104729)
            self._time_ms[site] = 0.0
            relay = SimulatedRelay(self, site)
            self._sites[site] = SiteResources(
                site_id=site,
                dps=SimulatedDPS(self, site),
                pmu=SimulatedPMU(self, site, relay),
                relay=relay,
                digital=SimulatedDigitalChannel(
                    self, site, levels=levels, timing=timing,
                    timing_map={item.name: item for item in self.timing_config.sets},
                ),
            )

    # ------------------------------------------------------------- accessors
    def site(self, site_id: int) -> SiteResources:
        try:
            return self._sites[site_id]
        except KeyError as exc:
            raise HALError(f"site {site_id} not instantiated", site=site_id) from exc

    @property
    def sites(self) -> dict[int, SiteResources]:
        """All instantiated sites."""
        return dict(self._sites)

    def bind_dut(self, site_id: int, dut: DUTModel) -> None:
        resources = self.site(site_id)
        self._duts[site_id] = dut
        resources.digital.dut = dut  # type: ignore[attr-defined]
        dut.power(0.0)

    def dut(self, site_id: int) -> DUTModel | None:
        """DUT bound to ``site_id``."""
        return self._duts.get(site_id)

    def pin_map(self, site_id: int) -> dict[str, int]:
        """Logical pin to physical channel map for one site."""
        return dict(self._pin_maps.get(site_id, {}))

    def rng_for(self, site_id: int) -> random.Random:
        """Per-site random generator (deterministic for a fixed seed)."""
        return self._rngs[site_id]

    def apply_noise(self, site_id: int, value: float, sigma: float | None = None) -> float:
        """Add relative Gaussian noise; ``sigma`` defaults to the configured value."""
        rel = self.noise_sigma if sigma is None else sigma
        if rel <= 0:
            return float(value)
        return float(value * (1.0 + self._rngs[site_id].gauss(0.0, rel)))

    # ------------------------------------------------------------------ time
    def add_time(self, site_id: int, milliseconds: float) -> None:
        """Accumulate simulated time (optionally really sleeping)."""
        with self._lock:
            self._time_ms[site_id] = self._time_ms.get(site_id, 0.0) + max(0.0, milliseconds)
        if self.sim_time_scale > 0 and milliseconds > 0:
            time.sleep(milliseconds * self.sim_time_scale / 1000.0)

    def elapsed_ms(self, site_id: int) -> float:
        return self._time_ms.get(site_id, 0.0)

    def reset_clock(self, site_id: int | None = None) -> None:
        with self._lock:
            if site_id is None:
                for key in self._time_ms:
                    self._time_ms[key] = 0.0
            else:
                self._time_ms[site_id] = 0.0

    # ----------------------------------------------------------------- infra
    def reset(self) -> None:
        for resources in self._sites.values():
            resources.relay.open_all()
            for pin in list(getattr(resources.dps, "_connected", ())):
                resources.dps.disconnect(pin)
            for pin in list(getattr(resources.pmu, "_connected", ())):
                resources.pmu.disconnect(pin)

    def self_check(self) -> list[str]:
        problems: list[str] = []
        for site_id, resources in self._sites.items():
            if self._duts.get(site_id) is None:
                problems.append(f"site {site_id}: no DUT bound")
            if not self._pin_maps.get(site_id):
                problems.append(f"site {site_id}: empty pin map")
        return problems

    def describe(self) -> dict[str, Any]:
        return {
            "backend": type(self).__name__,
            "sites": self.site_count,
            "noise_sigma": self.noise_sigma,
            "sim_time_scale": self.sim_time_scale,
            "seed": self.seed,
        }

    @classmethod
    def from_session(cls, session: Any, *, site_count: int | None = None) -> "SimulatedHAL":
        """Build a HAL from a loaded session."""
        return cls(
            site_count=site_count or session.site_count(),
            channel_map=session.channel_map,
            levels=session.levels,
            timing=session.timing,
            seed=session.run.seed,
            noise_sigma=session.sim.noise_sigma,
            sim_time_scale=session.sim.sim_time_scale,
        )


def disconnect_all(resources: Iterable[SiteResources]) -> None:
    """Utility used by teardown paths."""
    for item in resources:
        item.relay.open_all()
