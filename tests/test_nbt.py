"""NBT binary reader/writer: golden bytes, roundtrip, error contract."""

from __future__ import annotations

import struct

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats.nbt import (
    TAG_BYTE,
    TAG_COMPOUND,
    TAG_INT,
    TAG_LIST,
    TAG_LONG,
    TAG_STRING,
    ByteArray,
    Byte,
    Double,
    Float,
    Int,
    IntArray,
    ListOf,
    Long,
    LongArray,
    Short,
    gzip_compress,
    maybe_decompress,
    parse_nbt,
    tag_id_of,
    write_nbt,
)


def test_golden_empty_compound():
    assert write_nbt("", {}) == bytes([0x0A, 0x00, 0x00, 0x00])


def test_golden_int_field_big_endian():
    blob = write_nbt("", {"a": Int(1)})
    expected = bytes(
        [0x0A, 0x00, 0x00, 0x03, 0x00, 0x01, ord("a"), 0x00, 0x00, 0x00, 0x01, 0x00]
    )
    assert blob == expected


def test_root_name_written():
    blob = write_nbt("root", {})
    assert blob[0] == TAG_COMPOUND
    assert struct.unpack(">H", blob[1:3])[0] == 4
    assert blob[3:7] == b"root"


def test_roundtrip_preserves_types_and_bytes():
    tree = {
        "byte": Byte(-5),
        "short": Short(1234),
        "int": Int(-70000),
        "long": Long(1 << 40),
        "float": Float(1.5),
        "double": Double(2.25),
        "text": "hello 世界",
        "raw": ByteArray(b"\x00\xff\x10"),
        "ints": IntArray([1, -2, 3]),
        "longs": LongArray([1 << 40, -1]),
        "empty_compounds": ListOf(TAG_COMPOUND, []),
        "auto_list": [1, 2, 3],
        "nested": {"inner": {"deep": Int(42)}},
        "flag": True,
        "auto_long": 1 << 40,
    }
    blob = write_nbt("t", tree)
    name, parsed = parse_nbt(blob)
    assert name == "t"
    assert parsed["byte"] == -5
    assert parsed["short"] == 1234
    assert parsed["long"] == 1 << 40
    assert isinstance(parsed["longs"], LongArray)
    assert parsed["longs"] == [1 << 40, -1]
    assert parsed["text"] == "hello 世界"
    assert parsed["raw"] == b"\x00\xff\x10"
    assert parsed["empty_compounds"].elem_type == TAG_COMPOUND
    assert parsed["auto_list"].elem_type == TAG_INT
    assert parsed["flag"] == 1
    assert parsed["nested"]["inner"]["deep"] == 42
    # 字节级 roundtrip：parse -> write 还原原始类型
    assert write_nbt("t", parsed) == blob


def test_long_written_as_big_endian():
    blob = write_nbt("", {"v": Long(1)})
    assert struct.pack(">q", 1) in blob
    blob = write_nbt("", {"v": Int(-1)})
    assert b"\xff\xff\xff\xff" in blob


def test_gzip_roundtrip_and_determinism():
    raw = write_nbt("", {"x": Int(1)})
    gz = gzip_compress(raw)
    assert gz[:2] == b"\x1f\x8b"
    assert gzip_compress(raw) == gz
    assert maybe_decompress(gz) == raw
    assert maybe_decompress(raw) == raw
    name, tree = parse_nbt(gz)
    assert tree["x"] == 1
    assert parse_nbt(raw)[1]["x"] == 1


def test_empty_list_requires_wrapper():
    with pytest.raises(SchemaError, match="空列表"):
        write_nbt("", {"a": []})


def test_truncated_data_rejected():
    blob = write_nbt("", {"a": Int(1)})
    with pytest.raises(SchemaError, match="截断"):
        parse_nbt(blob[:-2])


def test_unknown_root_tag_rejected():
    with pytest.raises(SchemaError, match="根标签"):
        parse_nbt(bytes([0x7F, 0x00, 0x00]))


def test_broken_gzip_rejected():
    with pytest.raises(SchemaError, match="gzip"):
        parse_nbt(b"\x1f\x8b" + b"garbage")


def test_empty_data_rejected():
    with pytest.raises(SchemaError, match="为空"):
        parse_nbt(b"")


def test_string_length_guard():
    with pytest.raises(SchemaError, match="字符串"):
        write_nbt("", {"a": "x" * 70000})


def test_unsupported_type_rejected():
    with pytest.raises(SchemaError, match="不支持"):
        write_nbt("", {"a": object()})


def test_root_must_be_compound():
    with pytest.raises(SchemaError, match="根值"):
        write_nbt("", [1, 2])


def test_tag_id_inference():
    assert tag_id_of(1) == TAG_INT
    assert tag_id_of(1 << 40) == TAG_LONG
    assert tag_id_of(True) == TAG_BYTE
    assert tag_id_of("x") == TAG_STRING
    assert tag_id_of([1]) == TAG_LIST
    assert tag_id_of({"a": 1}) == TAG_COMPOUND
    with pytest.raises(SchemaError, match="空列表"):
        tag_id_of([])
    with pytest.raises(SchemaError):
        tag_id_of(object())


def test_list_of_rejects_bad_elem_type():
    with pytest.raises(SchemaError, match="元素类型"):
        ListOf(99, [])
