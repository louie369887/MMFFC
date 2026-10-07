"""原版 structure (.nbt) 构建/解析/检查/格式识别测试。"""

from __future__ import annotations

import pytest

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import AIR, Voxel, build_grid
from mmffc.formats.nbt import parse_nbt
from mmffc.formats.structure import (
    DEFAULT_DATA_VERSION,
    FORMAT_LITEMATIC,
    FORMAT_NBT,
    build_structure,
    detect_format,
    inspect_nbt_tree,
    parse_structure,
    read_palette,
    read_size,
)


def _voxels() -> list[Voxel]:
    return [
        Voxel(0, 0, 0, "minecraft:stone"),
        Voxel(1, 0, 0, "minecraft:oak_planks", (("facing", "north"),)),
        Voxel(0, 1, 0, "minecraft:glass"),
    ]


def _grid():
    return build_grid(_voxels(), origin=(0, 0, 0), size=(2, 2, 1))


def _tree(grid=None):
    blob = build_structure(grid if grid is not None else _grid())
    assert blob[:2] == b"\x1f\x8b"
    _, tree = parse_nbt(blob)
    return tree


def test_build_parse_roundtrip():
    grid = _grid()
    tree = _tree(grid)
    assert detect_format(tree) == FORMAT_NBT
    parsed = parse_structure(tree)
    assert parsed.size == grid.size
    assert parsed.origin == (0, 0, 0)
    assert parsed.palette == grid.palette
    assert parsed.cells == grid.cells


def test_dense_blocks_include_air():
    tree = _tree()
    volume = 2 * 2 * 1
    assert len(tree["blocks"]) == volume
    assert len(tree["palette"]) == 4  # air + 3
    # 每个 pos 唯一且覆盖全部格子
    positions = {tuple(entry["pos"]) for entry in tree["blocks"]}
    assert len(positions) == volume


def test_root_keys_order():
    tree = _tree()
    assert list(tree) == ["DataVersion", "size", "palette", "blocks", "entities"]


def test_data_version_default_and_custom():
    assert _tree()["DataVersion"] == DEFAULT_DATA_VERSION == 3465
    custom = parse_nbt(build_structure(_grid(), data_version=99))[1]
    assert custom["DataVersion"] == 99


def test_detect_format_litematic():
    assert detect_format({"Regions": {}}) == FORMAT_LITEMATIC


def test_detect_format_unknown():
    with pytest.raises(SchemaError, match="无法识别"):
        detect_format({"size": [1, 1, 1]})


def test_detect_format_not_dict():
    with pytest.raises(SchemaError, match="根节点"):
        detect_format([1, 2])


def test_inspect_nbt_tree():
    info = inspect_nbt_tree(_tree())
    assert info["format"] == FORMAT_NBT
    assert info["size"] == [2, 2, 1]
    assert info["volume"] == 4
    assert info["entry_count"] == 4
    assert info["block_count"] == 3
    assert info["palette_size"] == 4
    assert info["data_version"] == DEFAULT_DATA_VERSION
    assert info["entity_count"] == 0
    assert info["palette"][0] == {"name": AIR}
    planks = next(p for p in info["palette"] if p["name"] == "minecraft:oak_planks")
    assert planks["properties"] == {"facing": "north"}


def test_declared_size_kept_when_blocks_sparse():
    tree = {
        "DataVersion": 3465,
        "size": [3, 1, 1],
        "palette": [
            {"Name": AIR},
            {"Name": "minecraft:stone"},
        ],
        "blocks": [{"pos": [0, 0, 0], "state": 1}],
    }
    grid = parse_structure(tree)
    assert grid.size == (3, 1, 1)
    assert grid.origin == (0, 0, 0)
    assert grid.block_at(0, 0, 0) == ("minecraft:stone", ())
    assert grid.block_at(2, 0, 0) == (AIR, ())


def test_state_index_out_of_range():
    tree = {
        "size": [1, 1, 1],
        "palette": [{"Name": AIR}],
        "blocks": [{"pos": [0, 0, 0], "state": 5}],
    }
    with pytest.raises(SchemaError, match="越界"):
        parse_structure(tree)


def test_missing_blocks_rejected():
    tree = {"size": [1, 1, 1], "palette": [{"Name": AIR}]}
    with pytest.raises(SchemaError, match="缺少 blocks"):
        parse_structure(tree)


def test_empty_blocks_become_air():
    tree = {"size": [1, 1, 1], "palette": [{"Name": AIR}], "blocks": []}
    grid = parse_structure(tree)
    assert grid.size == (1, 1, 1)
    assert grid.block_at(0, 0, 0) == (AIR, ())


def test_read_size_errors():
    with pytest.raises(SchemaError, match="3 元素"):
        read_size([1, 1])
    with pytest.raises(SchemaError, match="必须是整数"):
        read_size([1, "2", 1])
    with pytest.raises(SchemaError, match="必须为正"):
        read_size([1, 0, 1])


def test_read_palette_errors():
    with pytest.raises(SchemaError, match="非空列表"):
        read_palette([])
    with pytest.raises(SchemaError, match="缺少 Name"):
        read_palette([{"Properties": {}}])
    with pytest.raises(SchemaError, match="Properties 必须是对象"):
        read_palette([{"Name": "minecraft:stone", "Properties": ["a"]}])
    with pytest.raises(SchemaError, match="字符串映射"):
        read_palette([{"Name": "minecraft:stone", "Properties": {"a": 1}}])
