"""Voxel NDJSON parsing, grid building and shape engine tests."""

from __future__ import annotations

import pytest

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import (
    AIR,
    MAX_VOLUME,
    Voxel,
    build_grid,
    dumps_ndjson,
    generate,
    grid_to_voxels,
    parse_ndjson,
)


# ----------------------------------------------------------------------
# NDJSON 解析
# ----------------------------------------------------------------------
def test_parse_valid_and_namespace_prefix():
    text = (
        '{"x": 0, "y": 1, "z": 2, "block": "stone"}\n'
        '{"x": 1, "y": 0, "z": 0, "block": "minecraft:oak_planks",'
        ' "properties": {"facing": "north", "half": "bottom"}}\n'
    )
    voxels = parse_ndjson(text)
    assert voxels[0].block == "minecraft:stone"
    assert voxels[1].properties == (
        ("facing", "north"),
        ("half", "bottom"),
    )


def test_parse_property_normalization():
    text = (
        '{"x":0,"y":0,"z":0,"block":"minecraft:oak_slab",'
        '"properties":{"waterlogged": true, "type": 2}}\n'
    )
    voxel = parse_ndjson(text)[0]
    assert dict(voxel.properties) == {
        "waterlogged": "true",
        "type": "2",
    }


def test_parse_property_null_rejected():
    with pytest.raises(SchemaError, match="必须是标量"):
        parse_ndjson('{"x":0,"y":0,"z":0,"block":"a","properties":{"k": null}}\n')


def test_parse_bom_tolerated():
    text = '\ufeff{"x":0,"y":0,"z":0,"block":"stone"}\n'
    assert len(parse_ndjson(text)) == 1


def test_parse_blank_lines_skipped():
    text = '\n{"x":0,"y":0,"z":0,"block":"stone"}\n\n'
    assert len(parse_ndjson(text)) == 1


def test_parse_error_carries_line_number():
    with pytest.raises(SchemaError, match="第 2 行 JSON"):
        parse_ndjson('{"x":0,"y":0,"z":0,"block":"a"}\n{broken\n')


def test_parse_unknown_field_rejected():
    with pytest.raises(SchemaError, match="第 1 行 未知字段"):
        parse_ndjson('{"x":0,"y":0,"z":0,"block":"a","extra":1}\n')


def test_parse_missing_axis_rejected():
    with pytest.raises(SchemaError, match="第 1 行 缺少字段 z"):
        parse_ndjson('{"x":0,"y":0,"block":"a"}\n')


def test_parse_non_int_coord_rejected():
    with pytest.raises(SchemaError, match="y 必须是整数"):
        parse_ndjson('{"x":0,"y":true,"z":0,"block":"a"}\n')


def test_parse_bad_block_id_rejected():
    with pytest.raises(SchemaError, match="非法方块 ID"):
        parse_ndjson('{"x":0,"y":0,"z":0,"block":"Stone"}\n')


def test_parse_duplicate_coord_rejected():
    text = (
        '{"x":0,"y":0,"z":0,"block":"stone"}\n'
        '{"x":0,"y":0,"z":0,"block":"dirt"}\n'
    )
    with pytest.raises(SchemaError, match="第 2 行.*与第 1 行重复"):
        parse_ndjson(text)


def test_parse_empty_rejected():
    with pytest.raises(SchemaError, match="为空"):
        parse_ndjson("\n  \n")


def test_dumps_roundtrip_sorted():
    voxels = [
        Voxel(1, 0, 0, "minecraft:stone"),
        Voxel(0, 1, 0, "minecraft:glass"),
        Voxel(0, 0, 1, "minecraft:dirt", (("facing", "north"),)),
    ]
    text = dumps_ndjson(voxels)
    reparsed = parse_ndjson(text)
    assert reparsed == sorted(voxels, key=lambda v: (v.y, v.z, v.x))
    assert [v.block for v in reparsed] == [
        "minecraft:stone",
        "minecraft:dirt",
        "minecraft:glass",
    ]


# ----------------------------------------------------------------------
# build_grid
# ----------------------------------------------------------------------
def test_grid_default_origin_is_min_corner():
    voxels = parse_ndjson(
        '{"x":5,"y":5,"z":5,"block":"stone"}\n'
    )
    grid = build_grid(voxels)
    assert grid.origin == (5, 5, 5)
    assert grid.size == (1, 1, 1)
    assert grid.palette[0] == (AIR, ())
    assert grid.block_count == 1


def test_grid_explicit_origin_pads_air():
    voxels = parse_ndjson('{"x":1,"y":1,"z":1,"block":"stone"}\n')
    grid = build_grid(voxels, origin=(0, 0, 0))
    assert grid.size == (2, 2, 2)
    assert grid.volume == 8
    assert grid.block_count == 1
    assert grid.block_at(1, 1, 1) == ("minecraft:stone", ())
    assert grid.block_at(0, 0, 0) == (AIR, ())


def test_grid_origin_beyond_min_rejected():
    voxels = parse_ndjson('{"x":1,"y":1,"z":1,"block":"stone"}\n')
    with pytest.raises(SchemaError, match="origin"):
        build_grid(voxels, origin=(2, 0, 0))


def test_grid_axis_limit():
    voxels = [
        Voxel(0, 0, 0, "minecraft:stone"),
        Voxel(600, 0, 0, "minecraft:stone"),
    ]
    with pytest.raises(SchemaError, match="轴长"):
        build_grid(voxels)


def test_grid_volume_limit():
    voxels = [
        Voxel(0, 0, 0, "minecraft:stone"),
        Voxel(511, 511, 511, "minecraft:stone"),
    ]
    grid_probe = 512 * 512 * 512
    assert grid_probe > MAX_VOLUME
    with pytest.raises(SchemaError, match="体积"):
        build_grid(voxels)


def test_grid_explicit_size_keeps_padding():
    voxels = parse_ndjson('{"x":0,"y":0,"z":0,"block":"stone"}\n')
    grid = build_grid(voxels, origin=(0, 0, 0), size=(4, 1, 1))
    assert grid.size == (4, 1, 1)
    assert grid.block_count == 1


def test_grid_explicit_size_too_small_rejected():
    voxels = parse_ndjson('{"x":3,"y":0,"z":0,"block":"stone"}\n')
    with pytest.raises(SchemaError, match="声明尺寸"):
        build_grid(voxels, origin=(0, 0, 0), size=(2, 1, 1))


def test_grid_to_voxels_excludes_air_by_default():
    voxels = parse_ndjson('{"x":0,"y":0,"z":0,"block":"stone"}\n')
    grid = build_grid(voxels, origin=(0, 0, 0), size=(3, 1, 1))
    assert len(grid_to_voxels(grid)) == 1
    assert len(grid_to_voxels(grid, include_air=True)) == 3


def test_grid_to_voxels_rebuilds_equal_grid():
    voxels = parse_ndjson(
        '{"x":0,"y":0,"z":0,"block":"stone"}\n'
        '{"x":1,"y":0,"z":0,"block":"glass"}\n'
    )
    grid = build_grid(voxels, origin=(0, 0, 0), size=(2, 1, 1))
    rebuilt = build_grid(grid_to_voxels(grid), origin=(0, 0, 0), size=(2, 1, 1))
    assert rebuilt.palette == grid.palette
    assert rebuilt.cells == grid.cells


# ----------------------------------------------------------------------
# 形状引擎
# ----------------------------------------------------------------------
def test_shape_box_solid():
    voxels = generate("x", {"ops": [
        {"shape": "box", "block": "stone", "x": 0, "y": 0, "z": 0, "w": 3, "h": 2, "d": 1},
    ]})
    assert len(voxels) == 6
    assert all(v.block == "minecraft:stone" for v in voxels)


def test_shape_hollow_box_shell_only():
    voxels = generate("x", {"ops": [
        {"shape": "hollow_box", "block": "stone", "x": 0, "y": 0, "z": 0,
         "w": 3, "h": 3, "d": 3},
    ]})
    assert len(voxels) == 26  # 27 - 中心
    assert all(v.coord != (1, 1, 1) for v in voxels)


def test_shape_sphere_solid():
    voxels = generate("x", {"ops": [
        {"shape": "sphere", "block": "glass", "cx": 0, "cy": 0, "cz": 0, "r": 1},
    ]})
    assert len(voxels) == 7  # 中心 + 6 轴向


def test_shape_hollow_sphere_skips_center():
    voxels = generate("x", {"ops": [
        {"shape": "hollow_sphere", "block": "glass", "cx": 0, "cy": 0, "cz": 0, "r": 2},
    ]})
    coords = {v.coord for v in voxels}
    assert (0, 0, 0) not in coords
    assert (1, 0, 0) not in coords  # 距离 1 <= (r-t) 不属于外壳
    assert (2, 0, 0) in coords
    assert len(coords) == 26


def test_shape_cylinder_axis_y_and_x():
    base = {"shape": "cylinder", "block": "stone", "cx": 0, "cy": 0, "cz": 0, "r": 1, "h": 2}
    ys = generate("x", {"ops": [dict(base, axis="y")]})
    xs = generate("x", {"ops": [dict(base, axis="x")]})
    assert len(ys) == 10  # 底面 5 x 高 2
    assert len(xs) == 10
    assert {v.y for v in ys} == {0, 1}
    assert {v.x for v in xs} == {0, 1}
    assert all(v.z in (0, -1, 1) for v in ys)


def test_shape_line_endpoints():
    voxels = generate("x", {"ops": [
        {"shape": "line", "block": "gold_block", "from": [0, 0, 0], "to": [5, 0, 0]},
    ]})
    assert len(voxels) == 6
    assert {(v.x, v.y, v.z) for v in voxels} == {(i, 0, 0) for i in range(6)}
    # 对角线连通
    diag = generate("x", {"ops": [
        {"shape": "line", "block": "stone", "from": [0, 0, 0], "to": [2, 2, 2]},
    ]})
    assert {(v.x, v.y, v.z) for v in diag} == {(0, 0, 0), (1, 1, 1), (2, 2, 2)}


def test_op_order_later_overrides_earlier():
    voxels = generate("x", {"ops": [
        {"shape": "box", "block": "stone", "x": 0, "y": 0, "z": 0, "w": 2, "h": 1, "d": 1},
        {"shape": "box", "block": "glass", "x": 0, "y": 0, "z": 0, "w": 1, "h": 1, "d": 1},
    ]})
    blocks = {(v.x, v.y, v.z): v.block for v in voxels}
    assert blocks[(0, 0, 0)] == "minecraft:glass"
    assert blocks[(1, 0, 0)] == "minecraft:stone"


def test_blocks_layer_overrides_ops():
    voxels = generate("x", {
        "ops": [{"shape": "box", "block": "stone", "x": 0, "y": 0, "z": 0, "w": 2, "h": 1, "d": 1}],
        "blocks": [{"x": 0, "y": 0, "z": 0, "block": "diamond_block"}],
    })
    blocks = {(v.x, v.y, v.z): v.block for v in voxels}
    assert blocks[(0, 0, 0)] == "minecraft:diamond_block"
    assert len(voxels) == 2


def test_generate_default_platform():
    voxels = generate("")
    assert len(voxels) == 25
    assert all(v.block == "minecraft:stone" and v.y == 0 for v in voxels)


def test_generate_unknown_shape_rejected():
    with pytest.raises(SchemaError, match="未知 shape"):
        generate("x", {"ops": [{"shape": "pyramid", "block": "stone"}]})


def test_generate_missing_param_rejected():
    with pytest.raises(SchemaError, match="缺少参数"):
        generate("x", {"ops": [{"shape": "box", "block": "stone", "x": 0, "y": 0, "z": 0}]})


def test_generate_bad_axis_rejected():
    with pytest.raises(SchemaError, match="axis 非法"):
        generate("x", {"ops": [
            {"shape": "cylinder", "cx": 0, "cy": 0, "cz": 0, "r": 1, "h": 1, "axis": "w"},
        ]})


def test_generate_bad_op_rejected():
    with pytest.raises(SchemaError, match="必须是对象"):
        generate("x", {"ops": ["box"]})
    with pytest.raises(SchemaError, match="缺少 shape"):
        generate("x", {"ops": [{"block": "stone"}]})
