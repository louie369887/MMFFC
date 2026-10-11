"""1.20.1 区块 NBT 构建/校验：位打包、Heightmaps、24 段语义。"""

from __future__ import annotations

import pytest

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import parse_ndjson
from mmffc.formats.chunk import (
    DATA_VERSION,
    HEIGHTMAP_BITS,
    HEIGHTMAP_KEYS,
    HEIGHTMAP_LONGS,
    MAX_Y,
    MIN_Y,
    SECTION_CEIL,
    SECTION_FLOOR,
    STATUS_FULL,
    _pack,
    _unpack,
    build_chunk,
    validate_chunk,
)
from mmffc.formats.nbt import parse_nbt

BLOCKS = (
    '{"x":0,"y":0,"z":0,"block":"stone"}\n'
    '{"x":1,"y":0,"z":0,"block":"oak_planks"}\n'
    '{"x":5,"y":63,"z":2,"block":"glass"}\n'
    '{"x":15,"y":19,"z":15,"block":"diamond_block","properties":{"axis":"y"}}\n'
)


def _chunk(text: str = BLOCKS, **kwargs):
    voxels = parse_ndjson(text)
    blob = build_chunk(0, 0, voxels, **kwargs)
    return parse_nbt(blob)[1]


def _cells(tree, section_y: int) -> list[dict]:
    section = tree["sections"][section_y - SECTION_FLOOR]
    assert section["Y"] == section_y
    return section


def _decoded_blocks(section: dict) -> dict[tuple[int, int, int], str]:
    """独立实现位解包，交叉验证 _pack。"""
    palette = section["block_states"]["palette"]
    data = section["block_states"].get("data")
    if data is None:
        return {}
    bits = max(4, (len(palette) - 1).bit_length())
    per_long = 64 // bits
    values: list[int] = []
    for long_value in data:
        for offset in range(per_long):
            values.append((long_value >> (offset * bits)) & ((1 << bits) - 1))
    names: dict[tuple[int, int, int], str] = {}
    for index, state in enumerate(values[:4096]):
        ly, rest = divmod(index, 256)
        lz, lx = divmod(rest, 16)
        names[(lx, ly, lz)] = palette[state]["Name"]
    return names


# ----------------------------------------------------------------------
# 布局
# ----------------------------------------------------------------------
def test_chunk_root_layout():
    tree = _chunk()
    assert tree["DataVersion"] == DATA_VERSION
    assert tree["xPos"] == 0 and tree["zPos"] == 0
    assert tree["yPos"] == SECTION_FLOOR == -4
    assert tree["Status"] == STATUS_FULL == "minecraft:full"
    assert tree["LastUpdate"] == 0 and tree["InhabitedTime"] == 0
    assert tree["block_entities"] == []
    assert set(tree["structures"]) == {"starts", "references"}


def test_all_24_sections_present():
    tree = _chunk()
    assert len(tree["sections"]) == 24
    assert [s["Y"] for s in tree["sections"]] == list(range(SECTION_FLOOR, SECTION_CEIL + 1))


def test_empty_section_has_single_air_palette_no_data():
    tree = _chunk()
    section = _cells(tree, SECTION_FLOOR)
    assert section["block_states"]["palette"] == [{"Name": "minecraft:air"}]
    assert "data" not in section["block_states"]
    assert section["biomes"] == {"palette": ["minecraft:plains"]}


def test_block_palette_is_sorted_and_data_present():
    tree = _chunk()
    section = _cells(tree, 0)
    palette = section["block_states"]["palette"]
    names = [entry["Name"] for entry in palette]
    assert names == ["minecraft:air", "minecraft:oak_planks", "minecraft:stone"]
    assert names == sorted(names)
    assert len(section["block_states"]["data"]) == 256  # 4 bits -> 16/long
    # properties 透传
    section = _cells(tree, 1)
    entry = section["block_states"]["palette"][1]
    assert entry == {"Name": "minecraft:diamond_block", "Properties": {"axis": "y"}}


def test_block_indices_roundtrip():
    tree = _chunk()
    assert _decoded_blocks(_cells(tree, 0))[(0, 0, 0)] == "minecraft:stone"
    assert _decoded_blocks(_cells(tree, 0))[(1, 0, 0)] == "minecraft:oak_planks"
    assert _decoded_blocks(_cells(tree, 0))[(2, 0, 0)] == "minecraft:air"
    assert _decoded_blocks(_cells(tree, 3))[(5, 15, 2)] == "minecraft:glass"
    assert _decoded_blocks(_cells(tree, 1))[(15, 3, 15)] == "minecraft:diamond_block"


def test_heightmap_values():
    tree = _chunk()
    for key in HEIGHTMAP_KEYS:
        longs = tree["Heightmaps"][key]
        assert len(longs) == HEIGHTMAP_LONGS == 37
        values = _unpack(longs, HEIGHTMAP_BITS)[:256]
        assert values[0] == 65  # y=0 -> 0+1+64
        assert values[2 * 16 + 5] == 128  # y=63
        assert values[3 * 16 + 3] == 0  # 空列


def test_heightmap_omitted_when_none():
    tree = _chunk(heightmap=False)
    assert "Heightmaps" not in tree


def test_heightmap_top_of_world():
    tree = _chunk('{"x":0,"y":319,"z":0,"block":"stone"}\n')
    assert _unpack(tree["Heightmaps"]["WORLD_SURFACE"], HEIGHTMAP_BITS)[0] == 384


def test_negative_y_section_mapping():
    tree = _chunk('{"x":0,"y":-64,"z":0,"block":"stone"}\n')
    section = _cells(tree, SECTION_FLOOR)
    assert _decoded_blocks(section)[(0, 0, 0)] == "minecraft:stone"
    assert _unpack(tree["Heightmaps"]["WORLD_SURFACE"], HEIGHTMAP_BITS)[0] == 1


def test_build_is_deterministic():
    first = build_chunk(0, 0, parse_ndjson(BLOCKS))
    second = build_chunk(0, 0, parse_ndjson(BLOCKS))
    assert first == second


def test_voxel_outside_chunk_rejected():
    voxels = parse_ndjson('{"x":16,"y":0,"z":0,"block":"stone"}\n')
    with pytest.raises(SchemaError, match="不属于区块"):
        build_chunk(0, 0, voxels)


def test_voxel_outside_height_rejected():
    voxels = parse_ndjson('{"x":0,"y":320,"z":0,"block":"stone"}\n')
    with pytest.raises(SchemaError, match="超出高度范围"):
        build_chunk(0, 0, voxels)
    low = parse_ndjson('{"x":0,"y":-65,"z":0,"block":"stone"}\n')
    with pytest.raises(SchemaError, match="超出高度范围"):
        build_chunk(0, 0, low)


def test_data_version_stamped_from_world():
    tree = _chunk(data_version=1)
    assert tree["DataVersion"] == 1


# ----------------------------------------------------------------------
# 位打包
# ----------------------------------------------------------------------
def test_pack_does_not_cross_long_boundary():
    # 9 bits：每 long 7 个值，不许跨 long
    values = [511] * 14
    longs = _pack(values, 9)
    assert len(longs) == 2
    assert longs[0] == (1 << 63) - 1  # 7 * 9 = 63 bits 全 1
    assert longs[1] == (1 << 63) - 1
    assert _unpack(longs, 9) == values


def test_pack_unpack_roundtrip_various_widths():
    for bits in (1, 4, 5, 6, 7, 9, 12):
        count = 333
        values = [index % (1 << bits) for index in range(count)]
        assert _unpack(_pack(values, bits), bits)[:count] == values


def test_pack_zero_is_zero():
    assert _pack([0] * 128, 4) == [0] * 8


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
def test_validate_accepts_good_chunk():
    assert validate_chunk(_chunk(), expect_x=0, expect_z=0) == []


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda t: t.update(Status="full"), "Status"),
        (lambda t: t.update(yPos=-5), "yPos"),
        (lambda t: t.update(xPos=7), "xPos"),
        (lambda t: t.update(zPos=7), "zPos"),
        (lambda t: t.update(sections=t["sections"][:23]), "sections"),
        (lambda t: t.pop("DataVersion"), "DataVersion"),
        (lambda t: t.pop("sections"), "sections"),
    ],
)
def test_validate_rejects_malformed(mutate, expected):
    tree = _chunk()
    mutate(tree)
    errors = validate_chunk(tree, expect_x=0, expect_z=0)
    assert errors
    assert any(expected in error for error in errors)


def test_validate_rejects_duplicate_section_y():
    tree = _chunk()
    tree["sections"][5]["Y"] = tree["sections"][4]["Y"]
    errors = validate_chunk(tree)
    assert any("缺少区块段 Y" in error for error in errors)


def test_validate_rejects_bad_palette_index():
    tree = _chunk()
    section = tree["sections"][4]  # Y=0
    section["block_states"]["data"][0] = 999  # 低 4 位解出索引 7
    errors = validate_chunk(tree)
    assert any("索引 7 越界" in error for error in errors)


def test_validate_rejects_wrong_data_length():
    tree = _chunk()
    tree["sections"][4]["block_states"]["data"].pop()
    errors = validate_chunk(tree)
    assert any("data 应有" in error for error in errors)


def test_validate_rejects_bad_heightmap_length():
    tree = _chunk()
    tree["Heightmaps"]["WORLD_SURFACE"].pop()
    errors = validate_chunk(tree)
    assert any("Heightmaps.WORLD_SURFACE" in error for error in errors)


def test_validate_rejects_out_of_range_heightmap_value():
    tree = _chunk()
    # 低 9 位全 1 -> 511 > 384
    tree["Heightmaps"]["MOTION_BLOCKING"][0] = 511
    errors = validate_chunk(tree)
    assert any("Heightmaps.MOTION_BLOCKING" in error for error in errors)


def test_validate_rejects_non_compound_root():
    assert validate_chunk("nope") == ["区块根节点必须是 compound"]


def test_validate_rejects_bad_biomes_palette():
    tree = _chunk()
    tree["sections"][4]["biomes"] = {"palette": [1, 2]}
    errors = validate_chunk(tree)
    assert any("biomes.palette" in error for error in errors)


def test_validate_data_version_mismatch():
    tree = _chunk()
    errors = validate_chunk(tree, data_version=1234)
    assert any("DataVersion=3465" in error for error in errors)
