"""Litematic 格式：位打包、往返、负 Size、元数据测试。

索引序回归重点：``index = (y*sizeZ + z)*sizeX + x``（x 最快），
``sx != sy`` 的结构此前曾因解码除数错误而旋转。
"""

from __future__ import annotations

import pytest

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import AIR, Grid, Voxel, build_grid, grid_to_voxels
from mmffc.formats.litematic import (
    LITEMATIC_VERSION,
    bits_per_block,
    build_litematic,
    inspect_litematic_tree,
    pack_block_states,
    parse_litematic_tree,
    unpack_block_states,
)
from mmffc.formats.nbt import parse_nbt
from mmffc.formats.structure import FORMAT_LITEMATIC, detect_format


# ----------------------------------------------------------------------
# 位宽与打包
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("palette_size", "expected"),
    [(1, 2), (2, 2), (3, 2), (4, 2), (5, 3), (8, 3), (9, 4), (16, 4), (17, 5)],
)
def test_bits_per_block(palette_size, expected):
    assert bits_per_block(palette_size) == expected


def test_pack_single_long_golden():
    assert pack_block_states([0, 1, 2, 3], 2) == [228]
    assert unpack_block_states([228], 2, 4) == [0, 1, 2, 3]


def test_pack_cross_long_all_ones():
    packed = pack_block_states([1] * 22, 3)
    assert len(packed) == 2
    expected0 = sum(1 << (3 * i) for i in range(21)) | (1 << 63)
    assert packed[0] == expected0 - (1 << 64)  # 有符号转换
    assert packed[1] == 0
    assert unpack_block_states(packed, 3, 22) == [1] * 22


def test_pack_cross_long_value_spans_boundary():
    # index 7 (0b111) 位于 bit 63..65：bit63 落 long0，其余落 long1
    packed = pack_block_states([7] * 22, 3)
    assert packed == [-1, 3]
    assert unpack_block_states(packed, 3, 22) == [7] * 22


def test_pack_rejects_out_of_range_index():
    with pytest.raises(SchemaError, match="越界"):
        pack_block_states([-1], 2)
    with pytest.raises(SchemaError, match="越界"):
        pack_block_states([4], 2)


def test_unpack_rejects_truncated():
    with pytest.raises(SchemaError, match="长度不足"):
        unpack_block_states([0], 3, 22)


# ----------------------------------------------------------------------
# 构建 / 解析往返
# ----------------------------------------------------------------------
def _floor_grid() -> Grid:
    """3x1x2 地板（sx != sy != sz，回归索引解码）。"""
    voxels = [
        Voxel(x, 0, z, "minecraft:stone") for x in range(3) for z in range(2)
    ]
    return build_grid(voxels)


def _blob(grid, **kwargs) -> bytes:
    kwargs.setdefault("timestamp_ms", 1700000000000)
    return build_litematic(grid, **kwargs)


def _roundtrip(grid, **kwargs) -> Grid:
    _, tree = parse_nbt(_blob(grid, **kwargs))
    return parse_litematic_tree(tree)


def test_roundtrip_asymmetric_floor():
    grid = _floor_grid()
    parsed = _roundtrip(grid)
    assert parsed.origin == grid.origin == (0, 0, 0)
    assert parsed.size == grid.size == (3, 1, 2)
    assert parsed.palette == grid.palette
    assert parsed.cells == grid.cells


def test_roundtrip_shifted_origin_two_blocks():
    grid = build_grid(
        [
            Voxel(3, 2, 1, "minecraft:stone"),
            Voxel(4, 2, 2, "minecraft:oak_planks", (("facing", "north"),)),
        ]
    )
    parsed = _roundtrip(grid)
    assert parsed.origin == (3, 2, 1)
    assert parsed.size == (2, 1, 2)
    assert parsed.palette == grid.palette
    assert parsed.cells == grid.cells


def test_roundtrip_tall_column():
    grid = build_grid(
        [Voxel(0, y, 0, "minecraft:stone") for y in range(6)]
    )
    parsed = _roundtrip(grid)
    assert parsed.size == (1, 6, 1)
    assert parsed.cells == grid.cells


def test_root_metadata_written():
    grid = _floor_grid()
    _, tree = parse_nbt(
        _blob(
            grid,
            name="My Build",
            author="Tester",
            description="hello",
            data_version=4325,
        )
    )
    assert detect_format(tree) == FORMAT_LITEMATIC
    assert tree["Version"] == LITEMATIC_VERSION == 6
    assert tree["SubVersion"] == 1
    assert tree["MinecraftDataVersion"] == 4325
    meta = tree["Metadata"]
    assert meta["Name"] == "My Build"
    assert meta["Author"] == "Tester"
    assert meta["Description"] == "hello"
    assert meta["RegionCount"] == 1
    assert meta["TotalVolume"] == 6
    assert meta["TotalBlocks"] == 6
    assert meta["TimeCreated"] == 1700000000000
    assert meta["TimeModified"] == 1700000000000
    assert meta["EnclosingSize"] == {"x": 3, "y": 1, "z": 2}
    region = tree["Regions"]["main"]
    assert region["Position"] == {"x": 0, "y": 0, "z": 0}
    assert region["Size"] == {"x": 3, "y": 1, "z": 2}
    assert region["BlockStatePalette"][0]["Name"] == AIR


def test_air_forced_to_palette_zero():
    # 防御性路径：手工构造空气不在 0 的网格
    grid = Grid(
        size=(1, 1, 1),
        palette=[("minecraft:stone", ()), (AIR, ())],
        cells=[0],
    )
    _, tree = parse_nbt(_blob(grid))
    region = tree["Regions"]["main"]
    assert region["BlockStatePalette"][0]["Name"] == AIR
    assert region["BlockStatePalette"][1]["Name"] == "minecraft:stone"
    assert unpack_block_states(
        list(region["BlockStates"]), bits_per_block(2), 1
    ) == [1]
    parsed = parse_litematic_tree(tree)
    assert parsed.block_at(0, 0, 0) == ("minecraft:stone", ())


def test_multi_region_merge():
    def region(x, block):
        return {
            "Position": {"x": x, "y": 0, "z": 0},
            "Size": {"x": 1, "y": 1, "z": 1},
            "BlockStatePalette": [{"Name": AIR}, {"Name": block}],
            "BlockStates": [1],  # index 1 @ 2 bits
            "TileEntities": [],
            "Entities": [],
        }

    tree = {
        "Version": 6,
        "Regions": {
            "a": region(0, "minecraft:stone"),
            "b": region(1, "minecraft:glass"),
        },
    }
    grid = parse_litematic_tree(tree)
    assert grid.origin == (0, 0, 0)
    assert grid.size == (2, 1, 1)
    assert grid.block_at(0, 0, 0) == ("minecraft:stone", ())
    assert grid.block_at(1, 0, 0) == ("minecraft:glass", ())


def test_negative_size_parsed_from_min_corner():
    tree = {
        "Regions": {
            "r": {
                "Position": {"x": 5, "y": 0, "z": 5},
                "Size": {"x": -2, "y": 1, "z": -2},
                "BlockStatePalette": [
                    {"Name": AIR},
                    {"Name": "minecraft:stone"},
                ],
                "BlockStates": [85],  # 4 格全为 index 1
                "TileEntities": [],
                "Entities": [],
            }
        }
    }
    grid = parse_litematic_tree(tree)
    assert grid.origin == (3, 0, 3)
    assert grid.size == (2, 1, 2)
    assert grid.block_count == 4
    coords = {v.coord for v in grid_to_voxels(grid)}
    assert coords == {(3, 0, 3), (4, 0, 3), (3, 0, 4), (4, 0, 4)}
    assert all(v.block == "minecraft:stone" for v in grid_to_voxels(grid))


def test_zero_size_rejected():
    tree = {
        "Regions": {
            "r": {
                "Position": {"x": 0, "y": 0, "z": 0},
                "Size": {"x": 0, "y": 1, "z": 1},
                "BlockStatePalette": [{"Name": AIR}],
                "BlockStates": [0],
            }
        }
    }
    with pytest.raises(SchemaError, match="不能含 0"):
        parse_litematic_tree(tree)


def test_missing_regions_rejected():
    with pytest.raises(SchemaError, match="缺少 Regions"):
        parse_litematic_tree({"Version": 6})


def test_palette_index_out_of_range_rejected():
    tree = {
        "Regions": {
            "r": {
                "Position": {"x": 0, "y": 0, "z": 0},
                "Size": {"x": 1, "y": 1, "z": 1},
                "BlockStatePalette": [{"Name": AIR}, {"Name": "minecraft:stone"}],
                "BlockStates": [3],  # bits=2, index 3 >= palette 2
            }
        }
    }
    with pytest.raises(SchemaError, match="越界"):
        parse_litematic_tree(tree)


def test_volume_limit_enforced_before_unpack():
    tree = {
        "Regions": {
            "r": {
                "Position": {"x": 0, "y": 0, "z": 0},
                "Size": {"x": 512, "y": 512, "z": 512},
                "BlockStatePalette": [{"Name": AIR}],
                "BlockStates": [0],
            }
        }
    }
    with pytest.raises(SchemaError, match="体积"):
        parse_litematic_tree(tree)


def test_inspect_litematic_tree():
    grid = _floor_grid()
    _, tree = parse_nbt(_blob(grid, name="Demo"))
    info = inspect_litematic_tree(tree)
    assert info["format"] == FORMAT_LITEMATIC
    assert info["version"] == LITEMATIC_VERSION
    assert info["sub_version"] == 1
    assert info["data_version"] == 3465
    assert info["name"] == "Demo"
    assert info["author"] == "MMFFC"
    assert info["region_count"] == 1
    assert info["total_volume"] == 6
    assert info["total_blocks"] == 6
    assert info["enclosing_size"] == [3, 1, 2]
    assert info["time_created"] == 1700000000000
    assert info["regions"] == [
        {"name": "main", "position": [0, 0, 0], "size": [3, 1, 2], "volume": 6}
    ]
