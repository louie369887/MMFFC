"""Minecraft 1.20.1 区块 NBT 构建与校验（CLI.md 3.4）。

格式要点（minecraft.wiki "Chunk format"，1.18+ 扁平化结构）：

* 根节点不再有 ``Level`` 包装；``DataVersion`` = 3465（1.20.1）
* ``yPos`` = -4；``sections`` 恒 24 个（Y = -4..19），空段也写出
* ``Status`` = ``minecraft:full``（命名空间值）
* ``block_states.palette`` 为 compound 列表（``Name`` + ``Properties``?），
  单元素时省略 ``data``；方块调色板最小 4 bits
* 位打包**逐 long 填充不跨 long**：``vpl = 64 // bits``，
  单段 ``ceil(4096 / vpl)`` 个 long；段内索引 YZX
  （``index = (y_local * 16 + z_local) * 16 + x_local``）
* ``biomes`` 单调色板（``["minecraft:plains"]``）时省略 ``data``
* Heightmaps：9 bits，每 long 7 值，共 37 longs；
  值 = 最高非空气方块的 Y + 65，空列恒 0

输出的 LastUpdate / InhabitedTime / structures 均为确定性空值，
区块更新由游戏自行接管。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import AIR, Voxel
from mmffc.formats.nbt import (
    Byte,
    Int,
    Long,
    LongArray,
    ListOf,
    TAG_COMPOUND,
    write_nbt,
)

DATA_VERSION = 3465
MIN_Y = -64
MAX_Y = 319
SECTION_FLOOR = -4
SECTION_CEIL = 19
SECTION_COUNT = SECTION_CEIL - SECTION_FLOOR + 1
SECTION_SIZE = 16
SECTION_VOLUME = SECTION_SIZE * SECTION_SIZE * SECTION_SIZE
STATUS_FULL = "minecraft:full"
DEFAULT_BIOME = "minecraft:plains"
AIR_STATE = (AIR, ())

MIN_PALETTE_BITS = 4
HEIGHTMAP_BITS = 9
HEIGHTMAP_KEYS = (
    "MOTION_BLOCKING",
    "MOTION_BLOCKING_NO_LEAVES",
    "OCEAN_FLOOR",
    "WORLD_SURFACE",
)
HEIGHTMAP_VALUES_PER_LONG = 64 // HEIGHTMAP_BITS
HEIGHTMAP_LONGS = math.ceil(256 / HEIGHTMAP_VALUES_PER_LONG)
HEIGHTMAP_MAX = MAX_Y + 65


def _bits_for(palette_size: int) -> int:
    """方块调色板位宽（最小 4 bits）。"""
    return max(MIN_PALETTE_BITS, (palette_size - 1).bit_length())


def _pack(values: list[int], bits: int) -> list[int]:
    """vanilla 位打包：每 long 装 ``64 // bits`` 个值，不跨 long。"""
    per_long = 64 // bits
    mask = (1 << bits) - 1
    longs: list[int] = []
    for start in range(0, len(values), per_long):
        acc = 0
        for offset, value in enumerate(values[start : start + per_long]):
            acc |= (value & mask) << (offset * bits)
        if acc >= 1 << 63:
            acc -= 1 << 64
        longs.append(acc)
    return longs


def _unpack(longs: Iterable[int], bits: int) -> list[int]:
    """:func:`_pack` 的逆运算（按 long 内偏移还原）。"""
    per_long = 64 // bits
    mask = (1 << bits) - 1
    values: list[int] = []
    for long_value in longs:
        raw = int(long_value) & ((1 << 64) - 1)
        for offset in range(per_long):
            values.append((raw >> (offset * bits)) & mask)
    return values


def _palette_entry(block: str, props: tuple[tuple[str, str], ...]) -> dict:
    entry: dict[str, Any] = {"Name": block}
    if props:
        entry["Properties"] = dict(props)
    return entry


def _section_parts(
    cells: dict[tuple[int, int, int], tuple[str, tuple[tuple[str, str], ...]]],
) -> tuple[list[tuple[str, tuple[tuple[str, str], ...]]], list[int], int]:
    """段内体素 -> (调色板状态表, 4096 稠密索引, 位宽)。"""
    if not cells:
        return [AIR_STATE], [], 4

    placed = sorted({state for state in cells.values()})
    palette: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    if len(cells) < SECTION_VOLUME or AIR_STATE in placed:
        palette.append(AIR_STATE)  # 空单元格隐式为空气
    palette.extend(state for state in placed if state != AIR_STATE)

    lookup = {state: index for index, state in enumerate(palette)}
    air_index = lookup.get(AIR_STATE)
    filler = air_index if air_index is not None else 0
    values = [filler] * SECTION_VOLUME
    for (lx, ly, lz), state in cells.items():
        values[(ly * SECTION_SIZE + lz) * SECTION_SIZE + lx] = lookup[state]
    return palette, values, _bits_for(len(palette))


def build_heightmaps(voxels: list[Voxel]) -> dict[str, LongArray]:
    """四种 heightmap 取同一近似值：最高非空气方块的 Y + 65。"""
    tops = [0] * 256
    for voxel in voxels:
        if voxel.is_air:
            continue
        column = (voxel.z & 15) * SECTION_SIZE + (voxel.x & 15)
        tops[column] = max(tops[column], voxel.y + 65)
    longs = _pack(tops, HEIGHTMAP_BITS)
    return {key: LongArray(longs) for key in HEIGHTMAP_KEYS}


def build_chunk(
    cx: int,
    cz: int,
    voxels: Iterable[Voxel],
    *,
    data_version: int = DATA_VERSION,
    heightmap: bool = True,
) -> bytes:
    """组装单个区块的裸 NBT 字节（未压缩）。"""
    placed = list(voxels)
    sections: list[dict[tuple[int, int, int], Any]] = [
        {} for _ in range(SECTION_COUNT)
    ]
    for voxel in placed:
        lx = voxel.x - cx * SECTION_SIZE
        lz = voxel.z - cz * SECTION_SIZE
        index = (voxel.y >> 4) - SECTION_FLOOR
        if not 0 <= lx < SECTION_SIZE or not 0 <= lz < SECTION_SIZE:
            raise SchemaError(
                f"体素 ({voxel.x},{voxel.y},{voxel.z}) 不属于区块 "
                f"({cx},{cz})"
            )
        if not 0 <= index < SECTION_COUNT:
            raise SchemaError(
                f"体素 Y={voxel.y} 超出高度范围 "
                f"[{MIN_Y},{MAX_Y}]"
            )
        sections[index][(lx, voxel.y & 15, lz)] = (voxel.block, voxel.properties)

    section_list = []
    for offset, cells in enumerate(sections):
        palette, values, bits = _section_parts(cells)
        palette_list = ListOf(
            TAG_COMPOUND,
            [_palette_entry(block, props) for block, props in palette],
        )
        block_states: dict[str, Any] = {"palette": palette_list}
        if values:
            block_states["data"] = LongArray(_pack(values, bits))
        section_list.append(
            {
                "Y": Byte(SECTION_FLOOR + offset),
                "block_states": block_states,
                "biomes": {"palette": [DEFAULT_BIOME]},
            }
        )

    root: dict[str, Any] = {
        "DataVersion": Int(data_version),
        "xPos": Int(cx),
        "yPos": Int(SECTION_FLOOR),
        "zPos": Int(cz),
        "Status": STATUS_FULL,
        "LastUpdate": Long(0),
        "InhabitedTime": Long(0),
        "sections": ListOf(TAG_COMPOUND, section_list),
        "block_entities": ListOf(TAG_COMPOUND, []),
        "structures": {"starts": {}, "references": {}},
    }
    if heightmap:
        root["Heightmaps"] = build_heightmaps(placed)
    return write_nbt("", root)


def _validate_section(section: Any, offset: int, seen: set[int]) -> list[str]:
    where = f"sections[{offset}]"
    if not isinstance(section, dict):
        return [f"{where} 必须是 compound"]
    errors: list[str] = []

    y_value = section.get("Y")
    if not isinstance(y_value, int) or isinstance(y_value, bool):
        errors.append(f"{where}: Y 缺失或非法")
    elif not SECTION_FLOOR <= y_value <= SECTION_CEIL:
        errors.append(f"{where}: Y={y_value} 超出 [-4,19]")
    else:
        seen.add(y_value)

    block_states = section.get("block_states")
    if not isinstance(block_states, dict):
        errors.append(f"{where}: 缺少 block_states")
    else:
        palette = block_states.get("palette")
        if not isinstance(palette, list) or not palette:
            errors.append(f"{where}: block_states.palette 必须是非空列表")
        else:
            for entry in palette:
                if not isinstance(entry, dict):
                    errors.append(f"{where}: palette 项必须是 compound")
                elif not isinstance(entry.get("Name"), str):
                    errors.append(f"{where}: palette 项缺少 Name")
            data = block_states.get("data")
            if len(palette) > 1:
                bits = _bits_for(len(palette))
                expected = math.ceil(SECTION_VOLUME / (64 // bits))
                if not isinstance(data, list):
                    errors.append(f"{where}: 缺少 block_states.data")
                elif len(data) != expected:
                    errors.append(
                        f"{where}: data 应有 {expected} 个 long，"
                        f"实际 {len(data)}"
                    )
                else:
                    for index in _unpack(data, bits)[:SECTION_VOLUME]:
                        if not 0 <= index < len(palette):
                            errors.append(
                                f"{where}: 方块索引 {index} 越界 "
                                f"(palette={len(palette)})"
                            )
                            break

    biomes = section.get("biomes")
    if not isinstance(biomes, dict):
        errors.append(f"{where}: 缺少 biomes")
    else:
        palette = biomes.get("palette")
        if not isinstance(palette, list) or not palette:
            errors.append(f"{where}: biomes.palette 必须是非空列表")
        elif not all(isinstance(item, str) for item in palette):
            errors.append(f"{where}: biomes.palette 必须是字符串列表")

    return errors


def validate_chunk(
    root: Any,
    *,
    expect_x: int | None = None,
    expect_z: int | None = None,
    data_version: int | None = None,
) -> list[str]:
    """区块语义校验，返回错误列表（空列表 = 通过）。"""
    if not isinstance(root, dict):
        return ["区块根节点必须是 compound"]

    errors: list[str] = []

    version = root.get("DataVersion")
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append("缺少或非法 DataVersion")
    elif data_version is not None and version != data_version:
        errors.append(f"DataVersion={version} 与 level.dat 的 {data_version} 不符")

    if root.get("Status") != STATUS_FULL:
        errors.append(f"Status 应为 {STATUS_FULL!r}，实际 {root.get('Status')!r}")

    y_pos = root.get("yPos")
    if not isinstance(y_pos, int) or isinstance(y_pos, bool):
        errors.append("缺少或非法 yPos")
    elif y_pos != SECTION_FLOOR:
        errors.append(f"yPos 应为 {SECTION_FLOOR}，实际 {y_pos}")

    for key, expected in (("xPos", expect_x), ("zPos", expect_z)):
        value = root.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"缺少或非法 {key}")
        elif expected is not None and value != expected:
            errors.append(f"{key} 应为 {expected}，实际 {value}")

    sections = root.get("sections")
    if not isinstance(sections, list):
        errors.append("缺少 sections 列表")
    elif len(sections) != SECTION_COUNT:
        errors.append(f"sections 应为 {SECTION_COUNT} 个，实际 {len(sections)}")
    else:
        seen: set[int] = set()
        for offset, section in enumerate(sections):
            errors.extend(_validate_section(section, offset, seen))
        missing = sorted(set(range(SECTION_FLOOR, SECTION_CEIL + 1)) - seen)
        if missing:
            errors.append(f"缺少区块段 Y: {missing}")

    heightmaps = root.get("Heightmaps")
    if heightmaps is not None:
        if not isinstance(heightmaps, dict):
            errors.append("Heightmaps 必须是 compound")
        else:
            for key in HEIGHTMAP_KEYS:
                array = heightmaps.get(key)
                if not isinstance(array, list):
                    errors.append(f"Heightmaps.{key} 缺失")
                    continue
                if len(array) != HEIGHTMAP_LONGS:
                    errors.append(
                        f"Heightmaps.{key} 应有 {HEIGHTMAP_LONGS} 个 long，"
                        f"实际 {len(array)}"
                    )
                    continue
                for value in _unpack(array, HEIGHTMAP_BITS)[:256]:
                    if not 0 <= value <= HEIGHTMAP_MAX:
                        errors.append(
                            f"Heightmaps.{key} 值 {value} 超出 "
                            f"[0,{HEIGHTMAP_MAX}]"
                        )
                        break

    return errors
