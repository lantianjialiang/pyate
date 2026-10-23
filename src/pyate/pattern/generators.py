"""Scripted ``.pat`` generators.

Every generator drives the real DUT model to compute expected values, so a
generated pattern is guaranteed to match the reference model.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from ..dut.models.adder import AdderModel
from ..dut.models.composite import CompositeModel
from ..dut.models.counter import CounterModel
from ..dut.models.lfsr import LfsrModel
from ..dut.models.shiftreg import ShiftRegModel
from ..dut.models.sram import SramModel

__all__ = [
    "counter_pattern", "shift_pattern", "composite_pattern", "counter_loop_pattern",
    "adder_pattern", "lfsr_pattern", "sram_pattern", "generate", "write_pattern",
    "GENERATORS",
]


def _bus(value: int, bits: int, *, compare: bool = True) -> str:
    """Format a bus value; leftmost character is bit 0.

    ``compare=True`` emits compare states (``L``/``H``), otherwise drive
    states (``0``/``1``).
    """
    one, zero = ("H", "L") if compare else ("1", "0")
    return "".join(one if (value >> index) & 1 else zero for index in range(bits))


def _header(name: str, pins: str, timing: str = "tset_fast", levels: str = "lv_nominal") -> list[str]:
    return [
        f"pattern {name};",
        f"pins    {pins};",
        f"timing  {timing};",
        f"levels  {levels};",
        "",
    ]


def counter_pattern(bits: int = 8, cycles: int = 16, name: str = "cnt_basic") -> str:
    """Reset the counter, then count up and compare every value."""
    dut = CounterModel({"bits": bits})
    dut.power(3.3)
    pins = "CLK, RSTN, EN, " + ", ".join(f"Q{i}" for i in range(bits))
    lines = _header(name, pins)
    lines.append("label start:")

    dut.set_inputs({"RSTN": 0, "EN": 0})
    dut.clock_cycle()
    lines.append(f"  [tset_fast] CLK=C RSTN=0 EN=0 Q={_bus(dut.value, bits)};")

    for _ in range(cycles):
        dut.set_inputs({"RSTN": 1, "EN": 1})
        dut.clock_cycle()
        lines.append(f"  [tset_fast] CLK=C RSTN=1 EN=1 Q={_bus(dut.value, bits)};")
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


def shift_pattern(bits: int = 8, name: str = "shift_walk1") -> str:
    """Walking-1 through the shift register, including ``SO``."""
    dut = ShiftRegModel({"bits": bits})
    dut.power(3.3)
    pins = "CLK, RSTN, SI, SO, " + ", ".join(f"Q{i}" for i in range(bits))
    lines = _header(name, pins)
    dut.set_inputs({"RSTN": 0, "SI": 0})
    dut.clock_cycle()
    lines.append(f"  CLK=C RSTN=0 SI=0 SO={'H' if dut.outputs()['SO'] else 'L'} Q={_bus(dut.value, bits)};")
    for index in range(bits):
        dut.set_inputs({"RSTN": 1, "SI": 1 if index == 0 else 0})
        dut.clock_cycle()
        so = "H" if dut.outputs()["SO"] else "L"
        lines.append(
            f"  CLK=C RSTN=1 SI={1 if index == 0 else 0} SO={so} Q={_bus(dut.value, bits)};"
        )
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


def composite_pattern(counter_bits: int = 8, shift_bits: int = 8, cycles: int = 16,
                      name: str = "cnt_basic") -> str:
    """Reset, count up and shift a walking one, comparing both blocks."""
    dut = CompositeModel({"counter_bits": counter_bits, "shift_bits": shift_bits})
    dut.power(3.3)
    pins = ["CLK", "RSTN", "EN", "SI", "SO"]
    pins += [f"Q{i}" for i in range(counter_bits)]
    pins += [f"S{i}" for i in range(shift_bits)]
    lines = _header(name, ", ".join(pins))

    def emit(rstn: int, en: int, si: int, label: str = "") -> str:
        states = {"RSTN": rstn, "EN": en, "SI": si}
        dut.set_inputs(states)
        outputs = dut.clock_cycle()
        q = sum(int(outputs[f"Q{i}"] or 0) << i for i in range(counter_bits))
        s = sum(int(outputs[f"S{i}"] or 0) << i for i in range(shift_bits))
        so = int(outputs["SO"] or 0)
        prefix = f"  {label}" if label else "  "
        return (
            f"{prefix}CLK=C RSTN={rstn} EN={en} SI={si} SO={'H' if so else 'L'} "
            f"Q={_bus(q, counter_bits)} S={_bus(s, shift_bits)};"
        )

    lines.append("label start:")
    lines.append(emit(0, 0, 0))
    for index in range(cycles):
        si = 1 if index < shift_bits and index % 2 == 0 else 0
        lines.append(emit(1, 1, si))
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


def counter_loop_pattern(bits: int = 8, name: str = "cnt_loop") -> str:
    """Endless counting loop, used by fmax / shmoo searches.

    The loop body unrolls one full counter period so the expected values stay
    valid when the ``jump`` wraps around.
    """
    engine = CounterModel({"bits": bits})
    engine.power(3.3)
    pins = "CLK, RSTN, EN, " + ", ".join(f"Q{i}" for i in range(bits))
    lines = _header(name, pins)
    lines.append("label start:")
    lines.append(f"  CLK=C RSTN=0 EN=0 Q={'X' * bits};")
    lines.append("label loop_top:")
    engine.set_inputs({"RSTN": 0, "EN": 0})
    engine.clock_cycle()
    for _ in range(min(1 << bits, 1024)):
        engine.set_inputs({"RSTN": 1, "EN": 1})
        engine.clock_cycle()
        lines.append(f"  CLK=C RSTN=1 EN=1 Q={_bus(engine.value, bits)};")
    lines.append("  jump loop_top;")
    lines.append("")
    return "\n".join(lines)


def adder_pattern(bits: int = 8, vectors: int = 64, seed: int = 7, name: str = "adder_rand") -> str:
    """Random A/B/CIN stimulus with expected sums."""
    dut = AdderModel({"bits": bits})
    dut.power(3.3)
    pins = ["CIN", "COUT"]
    pins += [f"A{i}" for i in range(bits)] + [f"B{i}" for i in range(bits)]
    pins += [f"S{i}" for i in range(bits)]
    rng = random.Random(seed)
    lines = _header(name, ", ".join(pins))
    for _ in range(vectors):
        a = rng.randrange(1 << bits)
        b = rng.randrange(1 << bits)
        cin = rng.randrange(2)
        states: dict[str, int] = {"CIN": cin}
        states.update({f"A{i}": (a >> i) & 1 for i in range(bits)})
        states.update({f"B{i}": (b >> i) & 1 for i in range(bits)})
        dut.set_inputs(states)
        outputs = dut.evaluate()
        s = sum(int(outputs[f"S{i}"] or 0) << i for i in range(bits))
        cout = int(outputs["COUT"] or 0)
        drive = " ".join(f"{pin}={value}" for pin, value in states.items())
        lines.append(f"  {drive} S={_bus(s, bits)} COUT={'H' if cout else 'L'};")
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


def lfsr_pattern(bits: int = 8, cycles: int = 32, name: str = "lfsr_seq") -> str:
    """Pseudo-random LFSR sequence."""
    dut = LfsrModel({"bits": bits})
    dut.power(3.3)
    pins = "CLK, RSTN, SO, " + ", ".join(f"Q{i}" for i in range(bits))
    lines = _header(name, pins)
    dut.set_inputs({"RSTN": 0})
    dut.clock_cycle()
    lines.append(f"  CLK=C RSTN=0 SO={'H' if dut.outputs()['SO'] else 'L'} Q={_bus(dut.value, bits)};")
    for _ in range(cycles):
        dut.set_inputs({"RSTN": 1})
        dut.clock_cycle()
        lines.append(f"  CLK=C RSTN=1 SO={'H' if dut.outputs()['SO'] else 'L'} Q={_bus(dut.value, bits)};")
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


def sram_pattern(depth: int = 64, width: int = 8, name: str = "sram_march") -> str:
    """Write every address then read it back (March element scaffold)."""
    dut = SramModel({"depth": depth, "width": width})
    dut.power(3.3)
    addr_bits = dut.addr_bits
    pins = ["CLK", "RSTN", "CEN", "WEN"]
    pins += [f"A{i}" for i in range(addr_bits)]
    pins += [f"D{i}" for i in range(width)] + [f"Q{i}" for i in range(width)]
    lines = _header(name, ", ".join(pins))

    for address in range(depth):
        data = (address * 3 + 1) & dut.data_mask
        lines.append(
            f"  CLK=C RSTN=1 CEN=0 WEN=0 A={_bus(address, addr_bits, compare=False)} "
            f"D={_bus(data, width, compare=False)} Q={'X' * width};"
        )
    for address in range(depth):
        data = (address * 3 + 1) & dut.data_mask
        lines.append(
            f"  CLK=C RSTN=1 CEN=0 WEN=1 A={_bus(address, addr_bits, compare=False)} "
            f"D={'X' * width} Q={_bus(data, width)};"
        )
    lines.append("  halt;")
    lines.append("")
    return "\n".join(lines)


GENERATORS: dict[str, Any] = {
    "counter": counter_pattern,
    "shiftreg": shift_pattern,
    "composite": composite_pattern,
    "counter_loop": counter_loop_pattern,
    "adder": adder_pattern,
    "lfsr": lfsr_pattern,
    "sram": sram_pattern,
}


def generate(name: str, **kwargs: Any) -> str:
    """Generate pattern text by generator name."""
    try:
        func = GENERATORS[name]
    except KeyError as exc:
        raise KeyError(f"unknown generator '{name}' (available: {', '.join(sorted(GENERATORS))})") from exc
    return func(**kwargs)


def write_pattern(directory: str | Path, filename: str, text: str) -> Path:
    """Write generated pattern text to ``directory/filename``."""
    target = Path(directory) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
