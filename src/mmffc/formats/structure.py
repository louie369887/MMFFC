"""原版 Minecraft 结构模板（.nbt）读写。

格式（minecraft.wiki Structure_file，1.10+）：

* gzip NBT，根为无名 compound
* ``DataVersion`` (Int)、``size`` (3xInt)、``palette``
  (List[Compound: ``Name`` + ``Properties``?])、``blocks``
  (List[Compound: ``pos`` 3xInt, ``state`` Int])、``entities`` (List)
* ``blocks`` 稠密覆盖全部格子（含空气），``state`` 为 palette 索引
"""

from __future__ import annotations

from typing import Any

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import AIR, Grid, Voxel, build_grid
from mmffc.formats.nbt import (
    Int,
    ListOf,
    TAG_COMPOUND,
    TAG_INT,
    gzip_compress,
    write_nbt,
)

DEFAULT_DATA_VERSION = 3465  # Minecraft 1.20.1

FORMAT_NBT = "nbt"
FORMAT_LITEMATIC = "litematic"


def detect_format(root: Any) -> str:
    """根据根 compound 键识别格式。"""
    if not isinstance(root, dict):
        raise SchemaError("结构根节点必须是 compound")
    if "Regions" in root:
        return FORMAT_LITEMATIC
    if "blocks" in root and "palette" in root:
        return FORMAT_NBT
    raise SchemaError("无法识别的结构文件格式（缺少 Regions 或 blocks/palette 键）")


def _require_int(value: Any, where: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{where} 必须是整数")
    if positive and value <= 0:
        raise SchemaError(f"{where} 必须为正: {value}")
    return value


def read_size(raw: Any, where: str = "size") -> tuple[int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise SchemaError(f"{where} 必须是 3 元素整数数组")
    return (
        _require_int(raw[0], f"{where}[0]", positive=True),
        _require_int(raw[1], f"{where}[1]", positive=True),
        _require_int(raw[2], f"{where}[2]", positive=True),
    )


def read_palette(raw: Any, where: str = "palette") -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """解析 [ {Name, Properties?} ] 调色板（vanilla 与 litematic 共用）。"""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SchemaError(f"{where} 必须是非空列表")
    entries: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for index, entry in enumerate(raw):
        entry_where = f"{where}[{index}]"
        if not isinstance(entry, dict):
            raise SchemaError(f"{entry_where} 必须是对象")
        name = entry.get("Name")
        if not isinstance(name, str) or not name:
            raise SchemaError(f"{entry_where} 缺少 Name")
        props_raw = entry.get("Properties")
        if props_raw is None:
            props: tuple[tuple[str, str], ...] = ()
        elif isinstance(props_raw, dict):
            items: list[tuple[str, str]] = []
            for key, value in props_raw.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise SchemaError(f"{entry_where}.Properties 必须是字符串映射")
                items.append((key, value))
            props = tuple(sorted(items))
        else:
            raise SchemaError(f"{entry_where}.Properties 必须是对象")
        entries.append((name, props))
    return entries


# ----------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------
def build_structure(grid: Grid, *, data_version: int = DEFAULT_DATA_VERSION) -> bytes:
    """Grid -> gzip .nbt 字节（稠密，含空气）。"""
    palette = []
    for block, props in grid.palette:
        entry: dict[str, Any] = {"Name": block}
        if props:
            entry["Properties"] = dict(props)
        palette.append(entry)

    sx, sy, sz = grid.size
    blocks: list[dict[str, Any]] = []
    index = 0
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                blocks.append({"pos": [x, y, z], "state": Int(grid.cells[index])})
                index += 1

    root = {
        "DataVersion": Int(data_version),
        "size": ListOf(TAG_INT, [sx, sy, sz]),
        "palette": ListOf(TAG_COMPOUND, palette),
        "blocks": ListOf(TAG_COMPOUND, blocks),
        "entities": ListOf(TAG_COMPOUND, []),
    }
    return gzip_compress(write_nbt("", root))


# ----------------------------------------------------------------------
# 解析 / 检查
# ----------------------------------------------------------------------
def parse_structure(root: Any) -> Grid:
    """根 compound -> Grid（保持文件声明的 size）。"""
    if not isinstance(root, dict):
        raise SchemaError("structure 根节点必须是对象")
    size = read_size(root.get("size"))
    palette = read_palette(root.get("palette"))
    blocks = root.get("blocks")
    if not isinstance(blocks, list):
        raise SchemaError("structure 缺少 blocks 列表")

    voxels: list[Voxel] = []
    for index, entry in enumerate(blocks):
        where = f"blocks[{index}]"
        if not isinstance(entry, dict):
            raise SchemaError(f"{where} 必须是对象")
        state = _require_int(entry.get("state"), f"{where}.state")
        if not 0 <= state < len(palette):
            raise SchemaError(f"{where}.state 调色板索引越界: {state} (palette={len(palette)})")
        pos = entry.get("pos")
        if not isinstance(pos, (list, tuple)) or len(pos) != 3:
            raise SchemaError(f"{where}.pos 必须是 3 元素整数数组")
        coords = tuple(_require_int(v, f"{where}.pos") for v in pos)
        block, props = palette[state]
        voxels.append(Voxel(coords[0], coords[1], coords[2], block, props))

    if not voxels:
        # 稠密语义：无条目视为空结构 -> 全空气
        voxels = [Voxel(0, 0, 0, AIR)]
    return build_grid(voxels, origin=(0, 0, 0), size=size)


def inspect_nbt_tree(root: Any) -> dict[str, Any]:
    """结构摘要（供 structure inspect）。"""
    if not isinstance(root, dict):
        raise SchemaError("structure 根节点必须是对象")
    size = read_size(root.get("size"))
    palette = read_palette(root.get("palette"))
    blocks = root.get("blocks", [])
    if not isinstance(blocks, list):
        raise SchemaError("structure 缺少 blocks 列表")

    non_air = 0
    for entry in blocks:
        if not isinstance(entry, dict):
            continue
        state = entry.get("state")
        if isinstance(state, int) and not isinstance(state, bool) and 0 <= state < len(palette):
            block, props = palette[state]
            if block != AIR or props:
                non_air += 1

    data_version = root.get("DataVersion")
    entities = root.get("entities")
    return {
        "format": FORMAT_NBT,
        "size": list(size),
        "volume": size[0] * size[1] * size[2],
        "entry_count": len(blocks),
        "block_count": non_air,
        "palette_size": len(palette),
        "palette": [
            {"name": block, **({"properties": dict(props)} if props else {})}
            for block, props in palette
        ],
        "data_version": (
            _require_int(data_version, "DataVersion")
            if isinstance(data_version, int) and not isinstance(data_version, bool)
            else None
        ),
        "entity_count": len(entities) if isinstance(entities, list) else 0,
    }
