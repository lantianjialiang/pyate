"""Built-in DUT models.  Importing this package registers every model."""

from __future__ import annotations

from ..base import DUTModel
from ..registry import register_dut
from .adder import AdderModel
from .composite import CompositeModel
from .counter import CounterModel
from .gates import GateArrayModel
from .lfsr import LfsrModel
from .shiftreg import ShiftRegModel
from .sram import SramModel

__all__ = [
    "AdderModel",
    "CompositeModel",
    "CounterModel",
    "GateArrayModel",
    "LfsrModel",
    "ShiftRegModel",
    "SramModel",
    "register",
]


def register(cls: type[DUTModel]) -> type[DUTModel]:
    """Register a model under its ``model_name``."""
    return register_dut(cls.model_name)(cls)


for _model in (
    CounterModel,
    ShiftRegModel,
    GateArrayModel,
    AdderModel,
    LfsrModel,
    SramModel,
    CompositeModel,
):
    register(_model)
