"""Anvil region (.mca) 读写：扇区布局、确定性、错误契约。"""

from __future__ import annotations

import struct
import zlib

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats.anvil import (
    HEADER_SIZE,
    MAX_SECTORS,
    REGION_CHUNKS,
    build_region,
    count_chunks,
    local_index,
    parse_region,
    region_filename,
)
from mmffc.formats.nbt import write_nbt


def _nbt(payload: bytes) -> bytes:
    """构造一个合法但与内容无关的 NBT 字节串（塞任意 payload）。"""
    return write_nbt("", {"pad": payload})


# ----------------------------------------------------------------------
# 索引与命名
# ----------------------------------------------------------------------
def test_local_index_matches_wiki():
    assert local_index(0, 0) == 0
    assert local_index(1, 0) == 1
    assert local_index(0, 1) == 32
    assert local_index(31, 31) == REGION_CHUNKS - 1
    # 负数区块按 & 31 折叠
    assert local_index(-1, -1) == 31 + 32 * 31
    assert local_index(32, 32) == 0


def test_region_filename():
    assert region_filename(0, 0) == "r.0.0.mca"
    assert region_filename(-1, 2) == "r.-1.2.mca"


# ----------------------------------------------------------------------
# 布局
# ----------------------------------------------------------------------
def test_empty_region_is_header_only():
    assert build_region({}) == bytes(HEADER_SIZE)


def test_single_chunk_sector_layout():
    nbt = _nbt(b"x" * 100)
    blob = build_region({0: nbt})
    assert len(blob) % 4096 == 0
    assert len(blob) == HEADER_SIZE + 4096  # 载荷占 1 个扇区
    # 位置表：扇区偏移 2、扇区数 1
    assert blob[0:3] == (2).to_bytes(3, "big")
    assert blob[3] == 1
    # 时间戳表清零
    assert blob[4096:HEADER_SIZE] == bytes(4096)
    # 载荷：4B 长度 + 压缩类型 2 + zlib 数据
    length = struct.unpack_from(">I", blob, HEADER_SIZE)[0]
    assert length == len(zlib.compress(nbt, 9)) + 1
    assert blob[HEADER_SIZE + 4] == 2
    assert zlib.decompress(blob[HEADER_SIZE + 5 : HEADER_SIZE + 4 + length]) == nbt


def test_chunk_length_excludes_padding():
    nbt = _nbt(b"y" * 5000)  # 超过一个扇区 -> 需要补零
    blob = build_region({5: nbt})
    length = struct.unpack_from(">I", blob, HEADER_SIZE)[0]
    sectors = blob[5 * 4 + 3]
    assert 4 + length <= sectors * 4096
    assert sectors * 4096 - (4 + length) < 4096
    assert len(blob) == HEADER_SIZE + sectors * 4096


def test_roundtrip_multiple_chunks():
    blobs = {
        local_index(3, 7): _nbt(b"alpha"),
        local_index(0, 0): _nbt(b"beta"),
        local_index(31, 2): _nbt(b"gamma"),
    }
    parsed = parse_region(build_region(blobs))
    assert parsed == blobs


def test_build_is_deterministic():
    blobs = {local_index(2, 3): _nbt(b"same bytes")}
    assert build_region(blobs) == build_region(blobs)


def test_chunks_written_in_index_order():
    blob = build_region({1: _nbt(b"a"), 0: _nbt(b"b")})
    assert blob[0:3] == (2).to_bytes(3, "big")  # index 0 先写
    second = struct.unpack_from(">I", blob, 1 * 4)[0] >> 8
    assert second == 3  # index 1 紧随其后（index 0 占 1 扇区）


def test_count_chunks():
    blob = build_region({0: _nbt(b"a"), 40: _nbt(b"b")})
    assert count_chunks(blob) == 2


# ----------------------------------------------------------------------
# 压缩类型兼容
# ----------------------------------------------------------------------
def _wrap_single(nbt: bytes, compression: int, payload: bytes) -> bytes:
    """手工构造只有 index 0 一个区块的 region 文件。"""
    header = bytearray(HEADER_SIZE)
    header[0:3] = (2).to_bytes(3, "big")
    header[3] = 1
    body = (
        struct.pack(">I", len(payload) + 1)
        + bytes([compression])
        + payload
    )
    body += b"\x00" * (4096 - len(body))
    return bytes(header) + body


def test_parse_gzip_and_raw_compression():
    import gzip

    nbt = _nbt(b"legacy")
    assert parse_region(_wrap_single(nbt, 1, gzip.compress(nbt))) == {0: nbt}
    assert parse_region(_wrap_single(nbt, 3, nbt)) == {0: nbt}


def test_parse_rejects_unknown_compression():
    with pytest.raises(SchemaError, match="压缩类型"):
        parse_region(_wrap_single(_nbt(b"z"), 7, b"??"))


# ----------------------------------------------------------------------
# 错误契约
# ----------------------------------------------------------------------
def test_parse_rejects_short_data():
    with pytest.raises(SchemaError, match="过短"):
        parse_region(bytes(100))


def test_parse_rejects_offset_in_header():
    blob = bytearray(build_region({}))
    blob[0:3] = (1).to_bytes(3, "big")
    blob[3] = 1
    with pytest.raises(SchemaError, match="头部区域"):
        parse_region(bytes(blob))


def test_parse_rejects_truncated_payload():
    blob = build_region({0: _nbt(b"data")})
    with pytest.raises(SchemaError, match="截断"):
        parse_region(blob[: HEADER_SIZE + 8])


def test_parse_rejects_sector_count_mismatch():
    import os

    blob = bytearray(build_region({0: os.urandom(5000)}))
    blob[3] = 1  # 实际需要 2 个扇区，谎报 1 个
    with pytest.raises(SchemaError, match="不一致"):
        parse_region(bytes(blob))


def test_parse_rejects_zero_sectors():
    blob = bytearray(build_region({0: _nbt(b"data")}))
    blob[3] = 0
    with pytest.raises(SchemaError, match="扇区数"):
        parse_region(bytes(blob))


def test_parse_corrupt_zlib_payload():
    import os

    nbt = os.urandom(200)
    blob = bytearray(_wrap_single(nbt, 2, zlib.compress(nbt, 9)))
    blob[HEADER_SIZE + 5] ^= 0xFF  # 破坏压缩数据首字节
    with pytest.raises(SchemaError, match="解压失败"):
        parse_region(bytes(blob))


def test_build_rejects_sector_overflow():
    # >255 扇区 ≈ 不可压缩的 >1 MiB 载荷
    import os

    with pytest.raises(SchemaError, match="255"):
        build_region({0: os.urandom(MAX_SECTORS * 4096 + 4096)})
