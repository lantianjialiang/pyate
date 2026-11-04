"""Stable physical identity and independent random streams."""
import hashlib
from dataclasses import dataclass
from typing import Any


def stable_seed(seed: int, identity: str, stream: str) -> int:
    """Derive a portable seed without Python's process-randomized hash()."""
    raw = f"{seed}\0{identity}\0{stream}".encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "little")


@dataclass(frozen=True)
class PhysicalPartId:
    """Identity survives movement between sockets, trays and test stages."""
    lot_id: str
    part_id: str

    @property
    def key(self) -> str:
        return f"{self.lot_id}/{self.part_id}"


@dataclass(frozen=True)
class DUTInstanceSpec:
    """Replayable population input; contact and measurement streams are separate."""
    identity: PhysicalPartId
    population_seed: int
    x_mm: float = 0.0
    y_mm: float = 0.0
    profile_json: str = ""
    wafer_radius_mm: float | None = None
    spatial_seed: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DUTInstanceSpec":
        """Restore the immutable physical description saved in the result journal."""
        values = dict(data)
        values["identity"] = PhysicalPartId(**values["identity"])
        return cls(**values)
