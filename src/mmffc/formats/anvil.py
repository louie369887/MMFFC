"""Anvil region (.mca) 文件读写（CLI.md 3.4）。

格式要点（minecraft.wiki "Region file format"，1.18+ 沿用）：

* 文件按 4 KiB 扇区组织；前 8 KiB 为头部
* 头部前 4096×4B 为位置表：3B 大端扇区偏移 + 1B 扇区数；
  位置表索引 = (cx & 31) + 32 * (cz & 31)
* 头部后 4096×4B 为时间戳表（确定性输出恒写 0）
* 区块载荷 = 4B 大端长度（**含**压缩类型字节）+ 1B 压缩类型
  （1=gzip, 2=zlib, 3=未压缩）+ 压缩数据，尾部补零到扇区边界
  （补零不计入长度）；单区块最多 255 个扇区

本模块只关心字节流：调用方负责把区块序列化为裸 NBT
（:func:`mmffc.formats.nbt.write_nbt`）再交给 :func:`build_region`。
"""

from __future__ import annotations

import gzip
import struct
import zlib

from mmffc.core.errors import SchemaError

SECTOR_SIZE = 4096
HEADER_SIZE = 8192
CHUNKS_PER_AXIS = 32
REGION_CHUNKS = CHUNKS_PER_AXIS * CHUNKS_PER_AXIS
MAX_SECTORS = 255

COMPRESSION_GZIP = 1
COMPRESSION_ZLIB = 2
COMPRESSION_NONE = 3

# 固定压缩级别：保证同一输入总是产出同一字节（CLI.md 2.1 规则 5）
ZLIB_LEVEL = 9


def local_index(cx: int, cz: int) -> int:
    """区块绝对坐标 -> 区域文件内的位置表索引（0..1023）。"""
    return (cx & 31) + CHUNKS_PER_AXIS * (cz & 31)


def region_filename(rx: int, rz: int) -> str:
    """区域坐标 -> 文件名（``r.<rx>.<rz>.mca``）。"""
    return f"r.{rx}.{rz}.mca"


def _decompress(compression: int, payload: bytes, where: str) -> bytes:
    try:
        if compression == COMPRESSION_GZIP:
            return gzip.decompress(payload)
        if compression == COMPRESSION_ZLIB:
            return zlib.decompress(payload)
        if compression == COMPRESSION_NONE:
            return payload
    except (OSError, EOFError, zlib.error) as exc:
        raise SchemaError(f"{where}: 解压失败: {exc}") from exc
    raise SchemaError(f"{where}: 未知的压缩类型 {compression}")


def build_region(chunks: dict[int, bytes]) -> bytes:
    """位置表索引 -> 裸 NBT 字节的映射，序列化为 region 文件字节。

    * 按索引升序写入（确定性）
    * 时间戳表恒为 0
    * 固定 zlib 级别
    """
    if len(chunks) > REGION_CHUNKS:
        raise SchemaError(
            f"region 区块数 {len(chunks)} 超过上限 {REGION_CHUNKS}"
        )

    locations = bytearray(REGION_CHUNKS * 4)
    stamps = bytearray(REGION_CHUNKS * 4)
    payloads: list[bytes] = []
    offset = HEADER_SIZE // SECTOR_SIZE

    for index in sorted(chunks):
        if not 0 <= index < REGION_CHUNKS:
            raise SchemaError(f"region 位置表索引越界: {index}")
        compressed = zlib.compress(chunks[index], ZLIB_LEVEL)
        body = (
            struct.pack(">I", len(compressed) + 1)
            + bytes([COMPRESSION_ZLIB])
            + compressed
        )
        sectors = (len(body) + SECTOR_SIZE - 1) // SECTOR_SIZE
        if sectors > MAX_SECTORS:
            raise SchemaError(
                f"区块 {index} 压缩后占 {sectors} 个扇区，"
                f"超过上限 {MAX_SECTORS}"
            )
        locations[index * 4 : index * 4 + 3] = offset.to_bytes(3, "big")
        locations[index * 4 + 3] = sectors
        payloads.append(body + b"\x00" * (sectors * SECTOR_SIZE - len(body)))
        offset += sectors

    return bytes(locations) + bytes(stamps) + b"".join(payloads)


def parse_region(data: bytes) -> dict[int, bytes]:
    """解析 region 文件，返回位置表索引 -> 解压后的裸 NBT 字节。"""
    if len(data) < HEADER_SIZE:
        raise SchemaError(
            f"region 数据过短: {len(data)} 字节 (< {HEADER_SIZE})"
        )

    chunks: dict[int, bytes] = {}
    for index in range(REGION_CHUNKS):
        start = int.from_bytes(data[index * 4 : index * 4 + 3], "big")
        sectors = data[index * 4 + 3]
        if start == 0 and sectors == 0:
            continue
        if start < HEADER_SIZE // SECTOR_SIZE:
            raise SchemaError(
                f"区块 {index}: 扇区偏移 {start} 落在头部区域内"
            )
        if sectors < 1:
            raise SchemaError(f"区块 {index}: 扇区数为 {sectors}")
        at = start * SECTOR_SIZE
        if at + 5 > len(data):
            raise SchemaError(f"区块 {index}: 扇区偏移越界")
        length = int.from_bytes(data[at : at + 4], "big")
        if length < 1:
            raise SchemaError(f"区块 {index}: 载荷长度 {length} 非法")
        if at + 4 + length > len(data):
            raise SchemaError(f"区块 {index}: 载荷被截断")
        if 4 + length > sectors * SECTOR_SIZE:
            raise SchemaError(
                f"区块 {index}: 载荷长度与扇区数 "
                f"{length}/{sectors} 不一致"
            )
        compression = data[at + 4]
        payload = data[at + 5 : at + 4 + length]
        chunks[index] = _decompress(compression, payload, f"区块 {index}")
    return chunks


def count_chunks(data: bytes) -> int:
    """只读位置表统计区块数（不解压，供 inspect 使用）。"""
    if len(data) < HEADER_SIZE:
        raise SchemaError(
            f"region 数据过短: {len(data)} 字节 (< {HEADER_SIZE})"
        )
    return sum(
        1
        for index in range(REGION_CHUNKS)
        if any(data[index * 4 : index * 4 + 4])
    )
