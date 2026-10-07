"""Litematica ``.litematic`` 格式（gzip NBT）读写。

规格依据 maruohon/litematica 源码（pre-rewrite/fabric/1.20.1 与
1.21.1-masa 分支）与 maruohon/litematica#53：

* 根：``Version``、``SubVersion``、``MinecraftDataVersion``、
  ``Metadata``、``Regions``
* **Version 写 6**：1.20.1 分支接受 1..6，1.21.x 分支接受 1..7
  （读取校验 ``version <= max``），6 对两端均兼容
* Metadata：``Name/Author/Description/RegionCount/TotalVolume/
  TotalBlocks/TimeCreated(Long)/TimeModified(Long)/EnclosingSize``
* Region：``Position{x,y,z}``（主/最小角）、``Size{x,y,z}``（可负，
  负方向表示从 Position 向负轴延伸；数据容器锚定最小角）、
  ``BlockStatePalette``（List[Compound: ``Name`` + ``Properties``?，
  **index 0 恒为 minecraft:air**）、``BlockStates``（LongArray）、
  ``TileEntities``/``Entities``（List）
* 位打包（LitematicaBitArray）：``bits = max(2, ceil(log2(paletteSize)))``，
  以 ``index * bits`` 为位偏移**跨 long 连续打包**（无逐 long 填充），
  LSB 优先；索引序 ``index = (y*sizeZ + z)*sizeX + x``
"""

from __future__ import annotations

import time
from typing import Any

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import AIR, Grid, MAX_VOLUME, Voxel, build_grid
from mmffc.formats.nbt import (
    Int,
    ListOf,
    Long,
    LongArray,
    TAG_COMPOUND,
    gzip_compress,
    write_nbt,
)
from mmffc.formats.structure import (
    DEFAULT_DATA_VERSION,
    FORMAT_LITEMATIC,
    read_palette,
    read_size,
)

LITEMATIC_VERSION = 6
LITEMATIC_SUB_VERSION = 1

_SIGNED_64 = 1 << 63
_UNSIGNED_64 = 1 << 64


def bits_per_block(palette_size: int) -> int:
    """每格位宽：min 2 位，按调色板大小取 ceil(log2(n))。"""
    if palette_size <= 1:
        return 2
    return max(2, (palette_size - 1).bit_length())


def pack_block_states(indices: list[int], bits: int) -> LongArray:
    """调色板索引 -> 跨 long 连续位打包（LSB 优先）。"""
    mask = (1 << bits) - 1
    long_count = (len(indices) * bits + 63) // 64
    values = [0] * long_count
    for i, index in enumerate(indices):
        if index < 0 or index > mask:
            raise SchemaError(f"调色板索引越界: {index} (bits={bits})")
        bit = i * bits
        long_index = bit >> 6
        offset = bit & 63
        values[long_index] |= (index << offset) & (_UNSIGNED_64 - 1)
        if offset + bits > 64:
            values[long_index + 1] |= index >> (64 - offset)
    return LongArray(
        value if value < _SIGNED_64 else value - _UNSIGNED_64 for value in values
    )


def unpack_block_states(longs: list[int], bits: int, count: int) -> list[int]:
    """跨 long 连续位打包 -> 调色板索引。"""
    mask = (1 << bits) - 1
    needed = (count * bits + 63) // 64
    if len(longs) < needed:
        raise SchemaError(
            f"BlockStates 长度不足: {len(longs)} longs (需要 {needed}, bits={bits})"
        )
    result: list[int] = []
    for i in range(count):
        bit = i * bits
        long_index = bit >> 6
        offset = bit & 63
        current = int(longs[long_index]) & (_UNSIGNED_64 - 1)
        value = (current >> offset) & mask
        if offset + bits > 64:
            if long_index + 1 >= len(longs):
                raise SchemaError(f"BlockStates 越界 (索引 {i})")
            following = int(longs[long_index + 1]) & (_UNSIGNED_64 - 1)
            value |= (following << (64 - offset)) & mask
        result.append(value)
    return result


def _xyz(raw: Any, where: str) -> tuple[int, int, int]:
    if not isinstance(raw, dict):
        raise SchemaError(f"{where} 必须是 {{x,y,z}} 对象")
    coords = []
    for axis in ("x", "y", "z"):
        value = raw.get(axis)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{where}.{axis} 必须是整数")
        coords.append(value)
    return (coords[0], coords[1], coords[2])


# ----------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------
def build_litematic(
    grid: Grid,
    *,
    name: str = "MMFFC Structure",
    author: str = "MMFFC",
    description: str = "",
    data_version: int = DEFAULT_DATA_VERSION,
    timestamp_ms: int | None = None,
    region_name: str = "main",
) -> bytes:
    """Grid -> gzip .litematic 字节（单 region，Position=最小角）。"""
    if not region_name:
        raise SchemaError("region_name 不能为空")

    # 防御：强制空气在调色板 0（litematica 规范）
    palette_keys = [(AIR, ())] + [key for key in grid.palette if key != (AIR, ())]
    lookup = {key: index for index, key in enumerate(palette_keys)}
    cells = [lookup[grid.palette[index]] for index in grid.cells]

    bits = bits_per_block(len(palette_keys))
    packed = pack_block_states(cells, bits)

    palette_entries: list[dict[str, Any]] = []
    for block, props in palette_keys:
        entry: dict[str, Any] = {"Name": block}
        if props:
            entry["Properties"] = dict(props)
        palette_entries.append(entry)

    sx, sy, sz = grid.size
    ox, oy, oz = grid.origin
    region = {
        "Position": {"x": Int(ox), "y": Int(oy), "z": Int(oz)},
        "Size": {"x": Int(sx), "y": Int(sy), "z": Int(sz)},
        "BlockStatePalette": ListOf(TAG_COMPOUND, palette_entries),
        "BlockStates": packed,
        "TileEntities": ListOf(TAG_COMPOUND, []),
        "Entities": ListOf(TAG_COMPOUND, []),
    }

    timestamp = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    metadata = {
        "Name": str(name),
        "Author": str(author),
        "Description": str(description),
        "RegionCount": Int(1),
        "TotalVolume": Int(grid.volume),
        "TotalBlocks": Int(grid.block_count),
        "TimeCreated": Long(timestamp),
        "TimeModified": Long(timestamp),
        "EnclosingSize": {"x": Int(sx), "y": Int(sy), "z": Int(sz)},
    }

    root = {
        "Version": Int(LITEMATIC_VERSION),
        "SubVersion": Int(LITEMATIC_SUB_VERSION),
        "MinecraftDataVersion": Int(data_version),
        "Metadata": metadata,
        "Regions": {region_name: region},
    }
    return gzip_compress(write_nbt("", root))


# ----------------------------------------------------------------------
# 解析 / 检查
# ----------------------------------------------------------------------
def parse_litematic_tree(root: Any) -> Grid:
    """根 compound -> Grid（多 region 合并；负 Size 支持）。"""
    if not isinstance(root, dict):
        raise SchemaError("litematic 根节点必须是对象")
    regions = root.get("Regions")
    if not isinstance(regions, dict) or not regions:
        raise SchemaError("litematic 缺少 Regions")

    voxels: list[Voxel] = []
    for region_name, region in regions.items():
        where = f"Regions.{region_name}"
        if not isinstance(region, dict):
            raise SchemaError(f"{where} 必须是对象")
        position = _xyz(region.get("Position"), f"{where}.Position")
        size_raw = _xyz(region.get("Size"), f"{where}.Size")
        extent = tuple(abs(v) for v in size_raw)
        if any(v == 0 for v in size_raw):
            raise SchemaError(f"{where}.Size 不能含 0: {size_raw}")
        if extent[0] * extent[1] * extent[2] > MAX_VOLUME:
            raise SchemaError(f"{where} 体积 {extent} 超出上限 {MAX_VOLUME}")
        min_corner = (
            position[0] + min(0, size_raw[0]),
            position[1] + min(0, size_raw[1]),
            position[2] + min(0, size_raw[2]),
        )

        palette = read_palette(region.get("BlockStatePalette"), f"{where}.BlockStatePalette")
        longs = region.get("BlockStates")
        if not isinstance(longs, list):
            raise SchemaError(f"{where} 缺少 BlockStates")

        volume = extent[0] * extent[1] * extent[2]
        bits = bits_per_block(len(palette))
        indices = unpack_block_states(longs, bits, volume)

        ex, ey, ez = extent
        for i, state in enumerate(indices):
            if state >= len(palette):
                raise SchemaError(
                    f"{where} 调色板索引越界: {state} (palette={len(palette)})"
                )
            # index = (y*sizeZ + z)*sizeX + x（y 层内 x 最快）
            layer, x = divmod(i, ex)
            y, z = divmod(layer, ez)
            block, props = palette[state]
            voxels.append(
                Voxel(min_corner[0] + x, min_corner[1] + y, min_corner[2] + z, block, props)
            )

    return build_grid(voxels)


def inspect_litematic_tree(root: Any) -> dict[str, Any]:
    """结构摘要（供 structure inspect）。"""
    if not isinstance(root, dict):
        raise SchemaError("litematic 根节点必须是对象")
    regions = root.get("Regions")
    if not isinstance(regions, dict) or not regions:
        raise SchemaError("litematic 缺少 Regions")
    metadata = root.get("Metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    region_rows: list[dict[str, Any]] = []
    total_volume = 0
    for region_name, region in regions.items():
        if not isinstance(region, dict):
            continue
        try:
            position = _xyz(region.get("Position"), "Position")
            size_raw = _xyz(region.get("Size"), "Size")
        except SchemaError:
            position, size_raw = (0, 0, 0), (0, 0, 0)
        extent = [abs(v) for v in size_raw]
        volume = extent[0] * extent[1] * extent[2]
        total_volume += volume
        region_rows.append(
            {
                "name": region_name,
                "position": list(position),
                "size": extent,
                "volume": volume,
            }
        )

    def _meta(key: str) -> Any:
        return metadata.get(key)

    enclosing = metadata.get("EnclosingSize")
    if isinstance(enclosing, dict):
        try:
            enclosing_size: list[int] | None = list(_xyz(enclosing, "EnclosingSize"))
        except SchemaError:
            enclosing_size = None
    else:
        enclosing_size = None

    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    return {
        "format": FORMAT_LITEMATIC,
        "version": _int_or_none(root.get("Version")),
        "sub_version": _int_or_none(root.get("SubVersion")),
        "data_version": _int_or_none(root.get("MinecraftDataVersion")),
        "name": _meta("Name") if isinstance(_meta("Name"), str) else None,
        "author": _meta("Author") if isinstance(_meta("Author"), str) else None,
        "description": _meta("Description") if isinstance(_meta("Description"), str) else None,
        "region_count": len(regions),
        "total_volume": _int_or_none(_meta("TotalVolume")) or total_volume,
        "total_blocks": _int_or_none(_meta("TotalBlocks")),
        "enclosing_size": enclosing_size,
        "time_created": _int_or_none(_meta("TimeCreated")),
        "time_modified": _int_or_none(_meta("TimeModified")),
        "regions": region_rows,
    }
