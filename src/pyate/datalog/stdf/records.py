"""STDF V4 record definitions and field encoders/decoders.

Field order and data types match the STDF V4 specification and are kept
binary compatible with ``pystdf`` (which is used by ``stdf verify``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

__all__ = [
    "RecordSpec", "RECORDS", "encode_record", "decode_record", "encode_dn",
    "normalize_endian",
    "FAR", "ATR", "MIR", "MRR", "PCR", "HBR", "SBR", "SDR", "WIR", "WRR",
    "PIR", "PRR", "PTR", "FTR", "TSR", "WCR",
]


@dataclass(frozen=True)
class RecordSpec:
    """A record type and its ordered fields."""

    name: str
    typ: int
    sub: int
    fields: tuple[tuple[str, str], ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Field names in order."""
        return tuple(name for name, _ in self.fields)


FAR = RecordSpec("FAR", 0, 10, (("CPU_TYPE", "U1"), ("STDF_VER", "U1")))
ATR = RecordSpec("ATR", 0, 20, (("MOD_TIM", "U4"), ("CMD_LINE", "Cn")))
MIR = RecordSpec("MIR", 1, 10, (
    ("SETUP_T", "U4"), ("START_T", "U4"), ("STAT_NUM", "U1"), ("MODE_COD", "C1"),
    ("RTST_COD", "C1"), ("PROT_COD", "C1"), ("BURN_TIM", "U2"), ("CMOD_COD", "C1"),
    ("LOT_ID", "Cn"), ("PART_TYP", "Cn"), ("NODE_NAM", "Cn"), ("TSTR_TYP", "Cn"),
    ("JOB_NAM", "Cn"), ("JOB_REV", "Cn"), ("SBLOT_ID", "Cn"), ("OPER_NAM", "Cn"),
    ("EXEC_TYP", "Cn"), ("EXEC_VER", "Cn"), ("TEST_COD", "Cn"), ("TST_TEMP", "Cn"),
    ("USER_TXT", "Cn"), ("AUX_FILE", "Cn"), ("PKG_TYP", "Cn"), ("FAMLY_ID", "Cn"),
    ("DATE_COD", "Cn"), ("FACIL_ID", "Cn"), ("FLOOR_ID", "Cn"), ("PROC_ID", "Cn"),
    ("OPER_FRQ", "Cn"), ("SPEC_NAM", "Cn"), ("SPEC_VER", "Cn"), ("FLOW_ID", "Cn"),
    ("SETUP_ID", "Cn"), ("DSGN_REV", "Cn"), ("ENG_ID", "Cn"), ("ROM_COD", "Cn"),
    ("SERL_NUM", "Cn"), ("SUPR_NAM", "Cn"),
))
MRR = RecordSpec("MRR", 1, 20, (
    ("FINISH_T", "U4"), ("DISP_COD", "C1"), ("USR_DESC", "Cn"), ("EXC_DESC", "Cn"),
))
PCR = RecordSpec("PCR", 1, 30, (
    ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("PART_CNT", "U4"), ("RTST_CNT", "U4"),
    ("ABRT_CNT", "U4"), ("GOOD_CNT", "U4"), ("FUNC_CNT", "U4"),
))
HBR = RecordSpec("HBR", 1, 40, (
    ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("HBIN_NUM", "U2"), ("HBIN_CNT", "U4"),
    ("HBIN_PF", "C1"), ("HBIN_NAM", "Cn"),
))
SBR = RecordSpec("SBR", 1, 50, (
    ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("SBIN_NUM", "U2"), ("SBIN_CNT", "U4"),
    ("SBIN_PF", "C1"), ("SBIN_NAM", "Cn"),
))
SDR = RecordSpec("SDR", 1, 80, (
    ("HEAD_NUM", "U1"), ("SITE_GRP", "U1"), ("SITE_CNT", "U1"), ("SITE_NUM", "k2U1"),
    ("HAND_TYP", "Cn"), ("HAND_ID", "Cn"), ("CARD_TYP", "Cn"), ("CARD_ID", "Cn"),
    ("LOAD_TYP", "Cn"), ("LOAD_ID", "Cn"), ("DIB_TYP", "Cn"), ("DIB_ID", "Cn"),
    ("CABL_TYP", "Cn"), ("CABL_ID", "Cn"), ("CONT_TYP", "Cn"), ("CONT_ID", "Cn"),
    ("LASR_TYP", "Cn"), ("LASR_ID", "Cn"), ("EXTR_TYP", "Cn"), ("EXTR_ID", "Cn"),
))
WIR = RecordSpec("WIR", 2, 10, (
    ("HEAD_NUM", "U1"), ("SITE_GRP", "U1"), ("START_T", "U4"), ("WAFER_ID", "Cn"),
))
WRR = RecordSpec("WRR", 2, 20, (
    ("HEAD_NUM", "U1"), ("SITE_GRP", "U1"), ("FINISH_T", "U4"), ("PART_CNT", "U4"),
    ("RTST_CNT", "U4"), ("ABRT_CNT", "U4"), ("GOOD_CNT", "U4"), ("FUNC_CNT", "U4"),
    ("WAFER_ID", "Cn"), ("FABWF_ID", "Cn"), ("FRAME_ID", "Cn"), ("MASK_ID", "Cn"),
    ("USR_DESC", "Cn"), ("EXC_DESC", "Cn"),
))
WCR = RecordSpec("WCR", 2, 30, (
    ("WAFR_SIZ", "R4"), ("DIE_HT", "R4"), ("DIE_WID", "R4"), ("WF_UNITS", "U1"),
    ("WF_FLAT", "C1"), ("CENTER_X", "I2"), ("CENTER_Y", "I2"), ("POS_X", "C1"),
    ("POS_Y", "C1"),
))
PIR = RecordSpec("PIR", 5, 10, (("HEAD_NUM", "U1"), ("SITE_NUM", "U1")))
PRR = RecordSpec("PRR", 5, 20, (
    ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("PART_FLG", "B1"), ("NUM_TEST", "U2"),
    ("HARD_BIN", "U2"), ("SOFT_BIN", "U2"), ("X_COORD", "I2"), ("Y_COORD", "I2"),
    ("TEST_T", "U4"), ("PART_ID", "Cn"), ("PART_TXT", "Cn"), ("PART_FIX", "Bn"),
))
PTR = RecordSpec("PTR", 15, 10, (
    ("TEST_NUM", "U4"), ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("TEST_FLG", "B1"),
    ("PARM_FLG", "B1"), ("RESULT", "R4"), ("TEST_TXT", "Cn"), ("ALARM_ID", "Cn"),
    ("OPT_FLAG", "B1"), ("RES_SCAL", "I1"), ("LLM_SCAL", "I1"), ("HLM_SCAL", "I1"),
    ("LO_LIMIT", "R4"), ("HI_LIMIT", "R4"), ("UNITS", "Cn"), ("C_RESFMT", "Cn"),
    ("C_LLMFMT", "Cn"), ("C_HLMFMT", "Cn"), ("LO_SPEC", "R4"), ("HI_SPEC", "R4"),
))
FTR = RecordSpec("FTR", 15, 20, (
    ("TEST_NUM", "U4"), ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("TEST_FLG", "B1"),
    ("OPT_FLAG", "B1"), ("CYCL_CNT", "U4"), ("REL_VADR", "U4"), ("REPT_CNT", "U4"),
    ("NUM_FAIL", "U4"), ("XFAIL_AD", "I4"), ("YFAIL_AD", "I4"), ("VECT_OFF", "I2"),
    ("RTN_ICNT", "U2"), ("PGM_ICNT", "U2"), ("RTN_INDX", "k12U2"),
    ("RTN_STAT", "k12n1"), ("PGM_INDX", "k13U2"), ("PGM_STAT", "k13n1"),
    ("FAIL_PIN", "Dn"), ("VECT_NAM", "Cn"), ("TIME_SET", "Cn"), ("OP_CODE", "Cn"),
    ("TEST_TXT", "Cn"), ("ALARM_ID", "Cn"), ("PROG_TXT", "Cn"), ("RSLT_TXT", "Cn"),
    ("PATG_NUM", "U1"), ("SPIN_MAP", "Dn"),
))
TSR = RecordSpec("TSR", 10, 30, (
    ("HEAD_NUM", "U1"), ("SITE_NUM", "U1"), ("TEST_TYP", "C1"), ("TEST_NUM", "U4"),
    ("EXEC_CNT", "U4"), ("FAIL_CNT", "U4"), ("ALRM_CNT", "U4"), ("TEST_NAM", "Cn"),
    ("SEQ_NAME", "Cn"), ("TEST_LBL", "Cn"), ("OPT_FLAG", "B1"), ("TEST_TIM", "R4"),
    ("TEST_MIN", "R4"), ("TEST_MAX", "R4"), ("TST_SUMS", "R4"), ("TST_SQRS", "R4"),
))

RECORDS: dict[str, RecordSpec] = {
    spec.name: spec for spec in (FAR, ATR, MIR, MRR, PCR, HBR, SBR, SDR, WIR,
                                 WRR, WCR, PIR, PRR, PTR, FTR, TSR)
}
_BY_KEY: dict[tuple[int, int], RecordSpec] = {(spec.typ, spec.sub): spec for spec in RECORDS.values()}

_PACK = {
    "U1": "<B", "U2": "<H", "U4": "<I", "I1": "<b", "I2": "<h", "I4": "<i",
    "R4": "<f", "R8": "<d",
}
_PACK_BE = {
    "U1": ">B", "U2": ">H", "U4": ">I", "I1": ">b", "I2": ">h", "I4": ">i",
    "R4": ">f", "R8": ">d",
}


def normalize_endian(endian: str) -> str:
    """Map ``"little"``/``"big"``/``"<"``/``">"`` onto a struct byte-order char."""
    return ">" if str(endian) in (">", "big", "be", "BE") else "<"


def _cn(value: Any) -> bytes:
    """Variable length ASCII string (1-byte length prefix)."""
    if value is None:
        raw = b""
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("ascii", errors="replace")
    raw = raw[:255]
    return bytes([len(raw)]) + raw


def _c1(value: Any) -> bytes:
    """Single fixed ASCII character."""
    if value is None:
        return b" "
    raw = value if isinstance(value, bytes) else str(value).encode("ascii", errors="replace")
    return (raw[:1] or b" ")


def _bn(value: Any) -> bytes:
    """Fixed bit field packed as bytes with a byte-count prefix."""
    if value is None:
        return b"\x00"
    raw = value if isinstance(value, bytes) else bytes(value)
    return bytes([len(raw)]) + raw


def encode_dn(bit_positions: Sequence[int], width: int) -> bytes:
    """Encode a ``Dn`` bit field (STDF bit count + packed bits)."""
    if not bit_positions:
        return b"\x00"
    nbytes = (max(max(bit_positions) + 1, width) + 7) // 8
    payload = bytearray(nbytes)
    for position in bit_positions:
        payload[position // 8] |= 1 << (position % 8)
    return bytes([nbytes * 8]) + bytes(payload)


def _dn(value: Any) -> bytes:
    """Encode ``Dn`` from ``bytes`` or ``(bit_positions, width)``."""
    if value is None:
        return b"\x00"
    if isinstance(value, bytes):
        return bytes([len(value) * 8]) + value
    positions, width = value
    return encode_dn(positions, width)


def split_k(stdf_type: str) -> tuple[int, str]:
    """Split a ``k<index><elem>`` STDF type into ``(count_field_index, elem)``."""
    body = stdf_type[1:]
    digits = ""
    while body and body[0].isdigit():
        digits += body[0]
        body = body[1:]
    if not digits:
        raise ValueError(f"bad array type '{stdf_type}'")
    return int(digits), body


def _array(stdf_type: str, value: Any, endian: str, count: int | None = None) -> bytes:
    """Encode a ``k<n><type>`` array.

    STDF arrays carry no length prefix: their element count comes from a separate
    counter field of the same record (``RTN_ICNT`` for ``RTN_INDX`` and so on).
    """
    _index, elem = split_k(stdf_type)
    items = list(value or [])
    size = int(count if count is not None else len(items))
    if elem in ("n1", "N1"):
        packed = bytearray((size + 1) // 2)
        for index, item in enumerate(items[:size]):
            nibble = int(item) & 0x0F
            if index % 2 == 0:
                packed[index // 2] = nibble
            else:
                packed[index // 2] |= nibble << 4
        return bytes(packed)
    fmt = (_PACK if normalize_endian(endian) == "<" else _PACK_BE).get(elem)
    if fmt is None:
        raise ValueError(f"unsupported array element type '{elem}'")
    return b"".join(struct.pack(fmt, int(item)) for item in items[:size])


def _encode_field(stdf_type: str, value: Any, endian: str) -> bytes:
    if stdf_type == "Cn":
        return _cn(value)
    if stdf_type == "C1":
        return _c1(value)
    if stdf_type == "B1":
        return bytes([int(value or 0) & 0xFF])
    if stdf_type == "Bn":
        return _bn(value)
    if stdf_type == "Dn":
        return _dn(value)
    if stdf_type.startswith("k"):
        return _array(stdf_type, value, endian)
    fmt = (_PACK if normalize_endian(endian) == "<" else _PACK_BE).get(stdf_type)
    if fmt is None:
        raise ValueError(f"unsupported STDF type '{stdf_type}'")
    if stdf_type.startswith("R"):
        return struct.pack(fmt, float(value or 0.0))
    return struct.pack(fmt, int(value or 0))


def encode_record(spec: RecordSpec, values: dict[str, Any], *,
                  endian: str = "little") -> bytes:
    """Encode one complete record (header included)."""
    chunks: list[bytes] = []
    for name, stdf_type in spec.fields:
        value = values.get(name)
        if stdf_type.startswith("k"):
            index, _elem = split_k(stdf_type)
            count_field = spec.fields[index][0]
            chunks.append(_array(stdf_type, value, endian, values.get(count_field)))
        else:
            chunks.append(_encode_field(stdf_type, value, endian))
    payload = b"".join(chunks)
    if len(payload) > 0xFFFF:
        raise ValueError(f"{spec.name} record too long ({len(payload)} bytes)")
    order = normalize_endian(endian)
    header = struct.pack(order + "HBB", len(payload), spec.typ, spec.sub)
    return header + payload


# --------------------------------------------------------------------- decode
def _decode_cn(buffer: bytes, offset: int) -> tuple[str, int]:
    length = buffer[offset]
    start = offset + 1
    return buffer[start:start + length].decode("ascii", errors="replace"), start + length


def decode_record(buffer: bytes, offset: int = 0) -> tuple[dict[str, Any], int, str]:
    """Decode one record from ``buffer``; returns ``(values, next_offset, name)``."""
    if offset + 4 > len(buffer):
        raise ValueError("truncated record header")
    length, typ, sub = struct.unpack_from("<HBB", buffer, offset)
    body_start = offset + 4
    body = buffer[body_start:body_start + length]
    spec = _BY_KEY.get((typ, sub))
    name = spec.name if spec else f"UNKNOWN({typ},{sub})"
    values: dict[str, Any] = {}
    if spec is None:
        return values, body_start + length, name
    pos = 0
    for field_name, stdf_type in spec.fields:
        try:
            if pos >= len(body):
                values[field_name] = None
                continue
            if stdf_type == "Cn":
                values[field_name], pos = _decode_cn(body, pos)
            elif stdf_type == "C1":
                values[field_name] = body[pos:pos + 1].decode("ascii", errors="replace")
                pos += 1
            elif stdf_type == "B1":
                values[field_name] = body[pos]
                pos += 1
            elif stdf_type == "Bn":
                count = body[pos]
                values[field_name] = body[pos + 1:pos + 1 + count]
                pos += 1 + count
            elif stdf_type == "Dn":
                bits = body[pos]
                count = (bits + 7) // 8
                values[field_name] = body[pos + 1:pos + 1 + count]
                pos += 1 + count
            elif stdf_type.startswith("k"):
                index, element = split_k(stdf_type)
                count = int(values.get(spec.fields[index][0]) or 0)
                if element in ("n1", "N1"):
                    raw = body[pos:pos + (count + 1) // 2]
                    pos += (count + 1) // 2
                    values[field_name] = [
                        (raw[i // 2] >> (4 * (i % 2))) & 0x0F for i in range(count)
                    ]
                else:
                    fmt = _PACK.get(element)
                    if fmt is None:
                        raise ValueError(f"unsupported array type '{stdf_type}'")
                    size = struct.calcsize(fmt)
                    values[field_name] = [
                        struct.unpack_from(fmt, body, pos + i * size)[0] for i in range(count)
                    ]
                    pos += count * size
            else:
                fmt = _PACK.get(stdf_type)
                if fmt is None:
                    raise ValueError(f"unsupported type '{stdf_type}'")
                size = struct.calcsize(fmt)
                values[field_name] = struct.unpack_from(fmt, body, pos)[0]
                pos += size
        except (IndexError, struct.error) as exc:
            raise ValueError(f"{name}.{field_name}: truncated field ({exc})") from exc
    return values, body_start + length, name


def iter_records(buffer: bytes) -> Iterable[tuple[str, dict[str, Any]]]:
    """Iterate every record of a buffer."""
    offset = 0
    while offset + 4 <= len(buffer):
        values, offset, name = decode_record(buffer, offset)
        yield name, values
