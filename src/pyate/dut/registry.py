"""DUT registry and factory.

Sampling a population (process variation + random defects) happens here so
that every scenario gets the exact same behaviour for the same seed.
"""

from __future__ import annotations

import random
from typing import Any, Callable, TypeVar

from .base import DUTModel, Defect
from .sampling import YieldSampler, mix_seed

__all__ = [
    "register_dut", "get_dut_class", "is_registered", "list_models", "create_dut",
    "YieldSampler", "mix_seed",
]

_REGISTRY: dict[str, type[DUTModel]] = {}
T = TypeVar("T", bound=type[DUTModel])
_BUILTINS_LOADED = False


def _ensure_builtins() -> None:
    """Import the built-in models once (they self-register)."""
    global _BUILTINS_LOADED
    if not _BUILTINS_LOADED:
        from . import models  # noqa: F401  (registration side effect)

        _BUILTINS_LOADED = True


def register_dut(name: str | None = None) -> Callable[[T], T]:
    """Class decorator registering a DUT model under ``name``."""

    def decorator(cls: T) -> T:
        key = name or getattr(cls, "model_name", cls.__name__.lower())
        _REGISTRY[str(key)] = cls
        cls.model_name = str(key)
        return cls

    return decorator


def get_dut_class(name: str) -> type[DUTModel]:
    """Look up a registered DUT class (raises ``KeyError`` when unknown)."""
    _ensure_builtins()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown DUT model '{name}' (available: {', '.join(sorted(_REGISTRY))})") from exc


def is_registered(name: str) -> bool:
    """True when ``name`` is a known DUT model."""
    _ensure_builtins()
    return name in _REGISTRY


def list_models() -> list[str]:
    """Every registered DUT model name."""
    _ensure_builtins()
    return sorted(_REGISTRY)


def _defect_from_spec(spec: Any) -> Defect:
    """Convert a config ``DefectSpec``/dict into a :class:`Defect`."""
    if isinstance(spec, Defect):
        return spec
    if hasattr(spec, "model_dump"):
        raw = spec.model_dump()
    else:
        raw = dict(spec)
    return Defect(
        kind=raw["kind"],
        pin=raw.get("pin"),
        pin2=raw.get("pin2"),
        value=raw.get("value"),
        prob=float(raw.get("prob", 1.0)),
        factor=float(raw.get("factor", 1.0)),
        na=float(raw.get("nA", raw.get("na", 100.0))),
    )


def create_dut(
    profile: Any,
    *,
    seed: int = 0,
    x: float = 0.0,
    y: float = 0.0,
    wafer_radius: float | None = None,
    sampler: YieldSampler | None = None,
    sample_yield: bool = True,
) -> DUTModel:
    """Instantiate a DUT from a ``DutProfile`` (or a compatible duck type).

    ``profile`` must expose ``model``, ``params``, ``defects``, ``yield_`` and
    ``process``.  ``x``/``y`` are die coordinates in millimetres, used by the
    spatial yield model.
    """
    cls = get_dut_class(profile.model)
    rng = random.Random(mix_seed(seed, x, y))
    dut = cls(dict(profile.params), rng)

    process = profile.process
    idd_scale = float(rng.lognormvariate(0.0, max(1e-6, process.idd_sigma)))
    leak_scale = float(rng.lognormvariate(0.0, max(1e-6, process.leak_sigma)))
    vth_shift = float(rng.gauss(0.0, process.vth_sigma))
    dut.apply_process_variation(vth_shift=vth_shift, idd_scale=idd_scale, leak_scale=leak_scale)

    defects = [_defect_from_spec(spec) for spec in profile.defects]
    if sample_yield:
        active_sampler = sampler
        if active_sampler is None:
            # Derive the sampler seed from the die coordinates so that calling
            # the factory once per die (CLI, validator, GUI) still gets an
            # independent draw per die instead of the same decision everywhere.
            active_sampler = YieldSampler(
                profile.yield_, mix_seed(seed, x, y), wafer_radius=wafer_radius, pin_source=dut
            )
        defects.extend(active_sampler.defects_for(x, y, dut))
    dut.inject_all(defects)
    return dut
