"""Unit tests for the STDF V4 record codec (encoder/decoder symmetry)."""

from __future__ import annotations

import struct

import pytest

from pyate.datalog.stdf.records import (
    ATR,
    FAR,
    FTR,
    HBR,
    MIR,
    PCR,
    PIR,
    PRR,
    PTR,
    SBR,
    SDR,
    RECORDS,
    RecordSpec,
    decode_record,
    encode_dn,
    encode_record,
    iter_records,
    normalize_endian,
    split_k,
)


def test_normalize_endian_accepts_both_spellings() -> None:
    assert normalize_endian("little") == "<"
    assert normalize_endian("<") == "<"
    assert normalize_endian("big") == ">"
    assert normalize_endian(">") == ">"


def test_split_k_reports_counter_index_and_element_type() -> None:
    assert split_k("k12U2") == (12, "U2")
    assert split_k("k2U1") == (2, "U1")
    assert split_k("k12n1") == (12, "n1")
    with pytest.raises(ValueError):
        split_k("kU1")


def test_encode_dn_packs_bit_positions_lsb_first() -> None:
    assert encode_dn([], 8) == b"\x00"
    encoded = encode_dn([0, 9], 16)
    assert encoded[0] == 16  # bit count
    assert encoded[1] == 0b0000_0001
    assert encoded[2] == 0b0000_0010


def test_far_roundtrip_carries_version() -> None:
    raw = encode_record(FAR, {"CPU_TYPE": 2, "STDF_VER": 4})
    assert raw[:4] == struct.pack("<HBB", 2, 0, 10)
    values, next_offset, name = decode_record(raw)
    assert name == "FAR"
    assert next_offset == len(raw)
    assert values["CPU_TYPE"] == 2
    assert values["STDF_VER"] == 4


def test_ptr_roundtrip_preserves_limits_and_flags() -> None:
    payload = {
        "TEST_NUM": 1001, "HEAD_NUM": 1, "SITE_NUM": 3, "TEST_FLG": 0x00,
        "PARM_FLG": 0x00, "RESULT": 1.2345, "TEST_TXT": "idd_standby",
        "ALARM_ID": "", "OPT_FLAG": 0x80, "RES_SCAL": -6, "LLM_SCAL": -6,
        "HLM_SCAL": -6, "LO_LIMIT": 10.0, "HI_LIMIT": 250.0, "UNITS": "mA",
        "C_RESFMT": "%.4f", "C_LLMFMT": "", "C_HLMFMT": "", "LO_SPEC": 0.0,
        "HI_SPEC": 300.0,
    }
    raw = encode_record(PTR, payload)
    values, offset, name = decode_record(raw)
    assert name == "PTR"
    assert offset == len(raw)
    for key in ("TEST_NUM", "HEAD_NUM", "SITE_NUM", "RESULT", "LO_LIMIT", "HI_LIMIT"):
        assert values[key] == pytest.approx(payload[key]) if isinstance(
            payload[key], float) else values[key] == payload[key]
    assert values["TEST_TXT"] == "idd_standby"
    assert values["UNITS"] == "mA"
    assert values["RES_SCAL"] == -6


def test_ftr_roundtrip_preserves_index_and_status_arrays() -> None:
    payload = {
        "TEST_NUM": 5000, "HEAD_NUM": 1, "SITE_NUM": 2, "TEST_FLG": 0x02,
        "OPT_FLAG": 0x00, "CYCL_CNT": 1234, "REL_VADR": 77, "REPT_CNT": 0,
        "NUM_FAIL": 2, "XFAIL_AD": -3, "YFAIL_AD": 4, "VECT_OFF": 0,
        "RTN_ICNT": 2, "PGM_ICNT": 2,
        "RTN_INDX": [3, 4], "RTN_STAT": [0, 1],
        "PGM_INDX": [3, 4], "PGM_STAT": [0, 1],
        "FAIL_PIN": ([0, 3], 16), "VECT_NAM": "walk1", "TIME_SET": "tset_fast",
        "OP_CODE": "", "TEST_TXT": "walking_one", "ALARM_ID": "",
        "PROG_TXT": "", "RSLT_TXT": "", "PATG_NUM": 1, "SPIN_MAP": None,
    }
    values, offset, name = decode_record(encode_record(FTR, payload))
    assert name == "FTR"
    assert offset == len(encode_record(FTR, payload))
    assert values["RTN_INDX"] == [3, 4]
    assert values["RTN_STAT"] == [0, 1]
    assert values["PGM_INDX"] == [3, 4]
    assert values["CYCL_CNT"] == 1234
    assert values["XFAIL_AD"] == -3
    assert values["FAIL_PIN"] == b"\x09\x00"
    assert values["VECT_NAM"] == "walk1"


def test_ftr_without_arrays_is_still_well_formed() -> None:
    payload = {
        "TEST_NUM": 1, "HEAD_NUM": 1, "SITE_NUM": 1, "TEST_FLG": 0, "OPT_FLAG": 0,
        "CYCL_CNT": 0, "REL_VADR": 0, "REPT_CNT": 0, "NUM_FAIL": 0,
        "XFAIL_AD": 0, "YFAIL_AD": 0, "VECT_OFF": 0,
        "RTN_ICNT": 0, "PGM_ICNT": 0, "RTN_INDX": [], "RTN_STAT": [],
        "PGM_INDX": [], "PGM_STAT": [], "FAIL_PIN": None,
    }
    raw = encode_record(FTR, payload)
    values, offset, _ = decode_record(raw)
    assert offset == len(raw)
    assert values["RTN_INDX"] == []
    assert values["FAIL_PIN"] == b""


def test_prr_roundtrip_keeps_bin_and_coordinates() -> None:
    payload = {
        "HEAD_NUM": 1, "SITE_NUM": 4, "PART_FLG": 0x00, "NUM_TEST": 42,
        "HARD_BIN": 1, "SOFT_BIN": 3, "X_COORD": -12, "Y_COORD": 34,
        "TEST_T": 9999, "PART_ID": "W01-D0012", "PART_TXT": "pass",
        "PART_FIX": b"\x01\x02",
    }
    values, offset, name = decode_record(encode_record(PRR, payload))
    assert name == "PRR"
    assert offset == 4 + struct.unpack_from("<H", encode_record(PRR, payload))[0]
    assert values["PART_FLG"] == 0
    assert values["NUM_TEST"] == 42
    assert values["HARD_BIN"] == 1
    assert values["SOFT_BIN"] == 3
    assert values["X_COORD"] == -12
    assert values["Y_COORD"] == 34
    assert values["PART_ID"] == "W01-D0012"
    assert values["PART_FIX"] == b"\x01\x02"


def test_sdr_site_number_array_is_consistent_with_site_count() -> None:
    payload = {
        "HEAD_NUM": 1, "SITE_GRP": 0, "SITE_CNT": 2, "SITE_NUM": [1, 2],
        "HAND_TYP": "prober", "HAND_ID": "P1", "CARD_TYP": "", "CARD_ID": "",
    }
    values, offset, name = decode_record(encode_record(SDR, payload))
    assert name == "SDR"
    assert values["SITE_CNT"] == 2
    assert values["SITE_NUM"] == [1, 2]
    assert values["HAND_TYP"] == "prober"
    assert offset == len(encode_record(SDR, payload))


def test_bin_summary_and_count_records_roundtrip() -> None:
    hbr = {"HEAD_NUM": 1, "SITE_NUM": 255, "HBIN_NUM": 1, "HBIN_CNT": 120,
           "HBIN_PF": "P", "HBIN_NAM": "pass"}
    sbr = {"HEAD_NUM": 1, "SITE_NUM": 255, "SBIN_NUM": 3, "SBIN_CNT": 7,
           "SBIN_PF": "F", "SBIN_NAM": "leakage"}
    pcr = {"HEAD_NUM": 1, "SITE_NUM": 255, "PART_CNT": 127, "RTST_CNT": 4,
           "ABRT_CNT": 0, "GOOD_CNT": 120, "FUNC_CNT": 120}
    for spec, payload in ((HBR, hbr), (SBR, sbr), (PCR, pcr)):
        values, offset, name = decode_record(encode_record(spec, payload))
        assert name == spec.name
        assert offset == len(encode_record(spec, payload))
        for key, expected in payload.items():
            assert values[key] == expected


def test_mir_and_atr_roundtrip_long_strings() -> None:
    mir = {
        "SETUP_T": 1, "START_T": 2, "STAT_NUM": 1, "MODE_COD": "P",
        "RTST_COD": " ", "PROT_COD": " ", "BURN_TIM": 0, "CMOD_COD": " ",
        "LOT_ID": "LOT-2024-01", "PART_TYP": "DEMO-ASIC", "NODE_NAM": "tester-1",
        "TSTR_TYP": "pyate", "JOB_NAM": "demo", "TST_TEMP": "25",
    }
    values, _offset, name = decode_record(encode_record(MIR, mir))
    assert name == "MIR"
    assert values["LOT_ID"] == "LOT-2024-01"
    assert values["TST_TEMP"] == "25"
    assert values["STAT_NUM"] == 1
    atr = {"MOD_TIM": 123456, "CMD_LINE": "pyate run session.yaml"}
    values, _offset, name = decode_record(encode_record(ATR, atr))
    assert name == "ATR"
    assert values["CMD_LINE"] == "pyate run session.yaml"


def test_cn_field_is_truncated_at_255_characters() -> None:
    long_name = "x" * 400
    values, _offset, _name = decode_record(encode_record(PIR, {"HEAD_NUM": 1, "SITE_NUM": 1}))
    assert values["SITE_NUM"] == 1
    values, _offset, _name = decode_record(
        encode_record(HBR, {"HEAD_NUM": 1, "SITE_NUM": 1, "HBIN_NUM": 1,
                            "HBIN_CNT": 1, "HBIN_PF": "P", "HBIN_NAM": long_name})
    )
    assert len(values["HBIN_NAM"]) == 255


def test_record_length_header_matches_payload_size() -> None:
    payload = {"HEAD_NUM": 2, "SITE_NUM": 5, "PART_FLG": 0, "NUM_TEST": 1,
               "HARD_BIN": 1, "SOFT_BIN": 1, "X_COORD": 0, "Y_COORD": 0,
               "TEST_T": 0, "PART_ID": "abc"}
    raw = encode_record(PRR, payload)
    declared = struct.unpack_from("<H", raw, 0)[0]
    assert declared == len(raw) - 4


def test_oversized_record_is_rejected() -> None:
    """A record longer than the 16-bit STDF length field must be refused."""
    wide = RecordSpec("WIDE", 91, 91, tuple((f"F{index}", "Cn") for index in range(300)))
    with pytest.raises(ValueError):
        encode_record(wide, {f"F{index}": "y" * 255 for index in range(300)})


def test_iter_records_walks_a_stream() -> None:
    stream = b"".join([
        encode_record(FAR, {"CPU_TYPE": 2, "STDF_VER": 4}),
        encode_record(PIR, {"HEAD_NUM": 1, "SITE_NUM": 1}),
        encode_record(PRR, {"HEAD_NUM": 1, "SITE_NUM": 1, "PART_FLG": 0,
                            "NUM_TEST": 0, "HARD_BIN": 1, "SOFT_BIN": 1}),
    ])
    names = [name for name, _values in iter_records(stream)]
    assert names == ["FAR", "PIR", "PRR"]


def test_unknown_record_is_skipped_but_consumed() -> None:
    unknown = struct.pack("<HBB", 3, 99, 99) + b"\x01\x02\x03"
    values, offset, name = decode_record(unknown, 0)
    assert values == {}
    assert name == "UNKNOWN(99,99)"
    assert offset == len(unknown)


def test_truncated_record_raises() -> None:
    with pytest.raises(ValueError):
        decode_record(b"\x00\x00", 0)
    raw = encode_record(PRR, {"HEAD_NUM": 1, "SITE_NUM": 1, "PART_FLG": 0,
                              "NUM_TEST": 1, "HARD_BIN": 1, "SOFT_BIN": 1})
    with pytest.raises(ValueError):
        decode_record(raw[:-6], 0)


def test_big_endian_encode_matches_struct_layout() -> None:
    raw_le = encode_record(FAR, {"CPU_TYPE": 2, "STDF_VER": 4}, endian="little")
    raw_be = encode_record(FAR, {"CPU_TYPE": 2, "STDF_VER": 4}, endian="big")
    assert raw_le == struct.pack("<HBB", 2, 0, 10) + b"\x02\x04"
    assert raw_be == struct.pack(">HBB", 2, 0, 10) + b"\x02\x04"


def test_unsupported_type_is_rejected() -> None:
    spec = RecordSpec("BOGUS", 90, 90, (("X", "Q9"),))
    with pytest.raises(ValueError):
        encode_record(spec, {"X": 1})


def test_all_declared_records_have_unique_keys() -> None:
    keys = {(spec.typ, spec.sub) for spec in RECORDS.values()}
    assert len(keys) == len(RECORDS)
