"""MCP tool tests (direct function calls; no transport)."""

from __future__ import annotations

import inspect

import anyio
import pytest
import responses
from mcp.server.mcpserver.exceptions import ToolError

from mmffc.formats import fancymenu as fm
from mmffc.formats import ftbquests as fq
from mmffc.formats import snbt as snbt_module
from mmffc.mcp import server as mcp_server
from mmffc.mcp.server import (
    complex_nbt_compiler,
    mod_search,
    voxelizer_brain,
    world_gen_rule_engine,
)

BUTTON = {
    "type": "button",
    "id": "play",
    "x": 50,
    "y": 60,
    "width": 80,
    "height": 20,
    "anchor": "center",
    "label": "Play",
}


# ----------------------------------------------------------------------
# complex_nbt_compiler
# ----------------------------------------------------------------------
def test_fancymenu_from_context():
    text = complex_nbt_compiler(
        target="fancymenu_layout",
        description="Main Menu",
        context={
            "layout_meta": {"random_mode": False},
            "elements": [BUTTON],
        },
    )
    assert text.startswith("type = fancymenu_layout")
    assert "[element:button:play]" in text
    assert "random_mode = false" in text
    doc = fm.parse_layout(text)
    assert fm.validate_layout(doc) == []


def test_fancymenu_template_from_description_only():
    text = complex_nbt_compiler(
        target="fancymenu_layout", description="A Fancy Menu"
    )
    doc = fm.parse_layout(text)
    assert fm.validate_layout(doc) == []
    assert doc.section("layout-meta").properties["name"] == "A Fancy Menu"


def test_fancymenu_unknown_element_type():
    with pytest.raises(ToolError, match="unknown_type"):
        complex_nbt_compiler(
            target="fancymenu_layout",
            description="X",
            context={"elements": [{"type": "unknown_type", "id": "x"}]},
        )


def test_fancymenu_bad_identifier():
    with pytest.raises(ToolError, match="非法元素标识符"):
        complex_nbt_compiler(
            target="fancymenu_layout",
            description="X",
            context={"elements": [{"type": "button", "id": "Bad ID"}]},
        )


def test_unknown_target():
    with pytest.raises(ToolError, match="未知的 target"):
        complex_nbt_compiler(target="nope")


def test_ftbquests_json5():
    text = complex_nbt_compiler(
        target="ftbquests_chapter",
        description="MMFFC Chapter",
        context={
            "id": "mmffc_chapter",
            "icon": "minecraft:book",
            "quests": [
                {
                    "id": "first_quest",
                    "name": "First Quest",
                    "description": "Get a diamond",
                    "icon": "minecraft:diamond",
                }
            ],
        },
    )
    chapter = fq.parse_chapter(text, fmt="json5")
    assert chapter["id"] == "mmffc_chapter"
    assert fq.validate_chapter(chapter) == []


def test_ftbquests_snbt_output():
    text = complex_nbt_compiler(
        target="ftbquests_chapter",
        description="MMFFC Chapter",
        context={
            "format": "snbt",
            "id": "mmffc_chapter",
            "quests": [
                {
                    "id": "q1",
                    "name": "Q1",
                    "description": "",
                    "icon": "minecraft:paper",
                }
            ],
        },
    )
    parsed = snbt_module.snbt_loads(text)
    assert parsed["id"] == "mmffc_chapter"
    assert fq.validate_chapter(parsed) == []


def test_ftbquests_validation_failure():
    with pytest.raises(ToolError, match="语义校验"):
        complex_nbt_compiler(
            target="ftbquests_chapter",
            description="X",
            context={
                "id": "ch1",
                "quests": [
                    {"id": "Bad ID!", "name": "n", "description": "",
                     "icon": "minecraft:book"}
                ],
            },
        )


def test_tool_signature_preserved():
    sig = inspect.signature(complex_nbt_compiler)
    assert list(sig.parameters) == ["target", "description", "context"]


def test_registered_tools():
    tools = anyio.run(mcp_server.server.list_tools)
    assert {tool.name for tool in tools} == {
        "complex_nbt_compiler",
        "mod_search",
        "mod_install",
        "voxelizer_brain",
        "world_gen_rule_engine",
    }


# ----------------------------------------------------------------------
# voxelizer_brain
# ----------------------------------------------------------------------
def test_voxelizer_default_platform():
    from mmffc.core.voxels import parse_ndjson

    text = voxelizer_brain("")
    voxels = parse_ndjson(text)
    assert len(voxels) == 25
    assert all(v.block == "minecraft:stone" and v.y == 0 for v in voxels)


def test_voxelizer_box_ops():
    from mmffc.core.voxels import parse_ndjson

    text = voxelizer_brain(
        "wall",
        {"ops": [{"shape": "box", "block": "stone",
                  "x": 0, "y": 0, "z": 0, "w": 3, "h": 2, "d": 1}]},
    )
    voxels = parse_ndjson(text)
    assert len(voxels) == 6
    assert all(v.block == "minecraft:stone" for v in voxels)


def test_voxelizer_blocks_override_ops():
    from mmffc.core.voxels import parse_ndjson

    text = voxelizer_brain(
        "x",
        {
            "ops": [{"shape": "box", "block": "stone",
                     "x": 0, "y": 0, "z": 0, "w": 2, "h": 1, "d": 1}],
            "blocks": [{"x": 0, "y": 0, "z": 0, "block": "diamond_block"}],
        },
    )
    voxels = parse_ndjson(text)
    blocks = {v.coord: v.block for v in voxels}
    assert blocks[(0, 0, 0)] == "minecraft:diamond_block"
    assert blocks[(1, 0, 0)] == "minecraft:stone"
    assert len(voxels) == 2


def test_voxelizer_invalid_shape():
    with pytest.raises(ToolError, match="未知 shape"):
        voxelizer_brain("x", {"ops": [{"shape": "pyramid"}]})


def test_voxelizer_missing_param():
    with pytest.raises(ToolError, match="缺少参数"):
        voxelizer_brain("x", {"ops": [{"shape": "box", "block": "stone"}]})


def test_voxelizer_output_roundtrips():
    from mmffc.core.voxels import parse_ndjson

    text = voxelizer_brain(
        "pillar",
        {"ops": [{"shape": "cylinder", "block": "obsidian",
                  "cx": 0, "cy": 0, "cz": 0, "r": 2, "h": 3, "axis": "y"}]},
    )
    voxels = parse_ndjson(text)
    assert len(voxels) == 39  # 底面 13 格 x 高 3


def test_voxelizer_tool_signature():
    sig = inspect.signature(voxelizer_brain)
    assert list(sig.parameters) == ["description", "context"]


# ----------------------------------------------------------------------
# world_gen_rule_engine
# ----------------------------------------------------------------------
def test_rule_engine_default_platform():
    import json

    from mmffc.core.worldgen import generate_from_rules

    text = anyio.run(world_gen_rule_engine, "")
    rules = json.loads(text)
    assert rules["name"] == "mmffc_world"
    assert rules["area"] == {"x": 0, "z": 0, "w": 16, "d": 16}
    assert rules["layers"] == [
        {"from": 0, "to": 0, "block": "minecraft:stone"}
    ]
    voxels = generate_from_rules(rules)
    assert len(voxels) == 256
    assert all(v.block == "minecraft:stone" and v.y == 0 for v in voxels)


def test_rule_engine_layers_and_ops():
    import json

    from mmffc.core.worldgen import generate_from_rules

    rules = json.loads(
        anyio.run(
            world_gen_rule_engine,
            "castle",
            {
                "area": {"x": 0, "z": 0, "w": 4, "d": 4},
                "layers": [{"from": -60, "to": -60, "block": "stone"}],
                "ops": [
                    {"shape": "box", "x": 0, "y": 0, "z": 0,
                     "w": 2, "h": 1, "d": 2, "block": "cobblestone"}
                ],
            },
        )
    )
    assert rules["name"] == "castle"
    assert [layer["from"] for layer in rules["layers"]] == [-60]
    voxels = generate_from_rules(rules)
    by_coord = {v.coord: v.block for v in voxels}
    assert by_coord[(0, 0, 0)] == "minecraft:cobblestone"
    assert by_coord[(1, 0, 1)] == "minecraft:cobblestone"
    assert by_coord[(3, -60, 0)] == "minecraft:stone"
    assert by_coord[(0, -60, 3)] == "minecraft:stone"
    assert (3, 0, 0) not in by_coord  # layer 在 y=-60，ops 只覆盖 2x2


def test_rule_engine_layers_override_order():
    import json

    from mmffc.core.worldgen import generate_from_rules

    rules = json.loads(
        anyio.run(
            world_gen_rule_engine,
            "",
            {
                "area": {"x": 0, "z": 0, "w": 1, "d": 1},
                "layers": [
                    {"from": 0, "to": 0, "block": "dirt"},
                    {"from": 0, "to": 0, "block": "sand"},
                ],
            },
        )
    )
    assert [v.block for v in generate_from_rules(rules)] == [
        "minecraft:sand"
    ]


def test_rule_engine_blocks_win_over_ops():
    import json

    from mmffc.core.worldgen import generate_from_rules

    rules = json.loads(
        anyio.run(
            world_gen_rule_engine,
            "",
            {
                "ops": [
                    {"shape": "box", "x": 0, "y": 0, "z": 0,
                     "w": 1, "h": 1, "d": 1, "block": "stone"}
                ],
                "blocks": [
                    {"x": 0, "y": 0, "z": 0, "block": "diamond_block"}
                ],
            },
        )
    )
    assert generate_from_rules(rules)[0].block == "minecraft:diamond_block"


def test_rule_engine_unknown_key_rejected():
    with pytest.raises(ToolError, match="未知字段"):
        anyio.run(
            world_gen_rule_engine, "x", {"layers": [], "oops": 1}
        )


def test_rule_engine_layers_require_area():
    with pytest.raises(ToolError, match="area"):
        anyio.run(
            world_gen_rule_engine,
            "x",
            {"layers": [{"from": 0, "to": 0, "block": "stone"}]},
        )


def test_rule_engine_air_only_rejected():
    with pytest.raises(ToolError, match="空气"):
        anyio.run(
            world_gen_rule_engine,
            "x",
            {"blocks": [{"x": 0, "y": 0, "z": 0, "block": "air"}]},
        )


def test_rule_engine_bad_block_rejected():
    with pytest.raises(ToolError, match="方块 ID"):
        anyio.run(
            world_gen_rule_engine,
            "x",
            {"blocks": [{"x": 0, "y": 0, "z": 0, "block": "not a block!"}]},
        )


def test_rule_engine_layer_out_of_y_range():
    with pytest.raises(ToolError, match="必须落在"):
        anyio.run(
            world_gen_rule_engine,
            "x",
            {
                "area": {"x": 0, "z": 0, "w": 1, "d": 1},
                "layers": [{"from": 400, "to": 401, "block": "stone"}],
            },
        )


def test_rule_engine_output_is_key_sorted():
    import json

    text = anyio.run(world_gen_rule_engine, "")
    assert text == json.dumps(
        json.loads(text), ensure_ascii=False, indent=2, sort_keys=True
    )


def test_rule_engine_progress_callback():
    """progress 回调单调递增并在结束时等于总数。"""
    import json

    from mmffc.core.worldgen import generate_from_rules

    rules = json.loads(anyio.run(world_gen_rule_engine, ""))
    seen: list[tuple[int, int, str]] = []
    generate_from_rules(
        rules, progress=lambda done, total, phase: seen.append((done, total, phase))
    )
    assert seen, "进度回调未被调用"
    dones = [item[0] for item in seen]
    assert dones == sorted(dones)  # 单调递增
    assert dones[-1] == seen[-1][1]  # 结束即总数
    assert {item[2] for item in seen} >= {"ops", "blocks"}
    assert seen[0][2].startswith("layers[")


def test_rule_engine_tool_signature():
    sig = inspect.signature(world_gen_rule_engine)
    assert list(sig.parameters) == ["description", "context", "ctx"]
    assert inspect.iscoroutinefunction(world_gen_rule_engine)


def test_rule_engine_context_hidden_from_schema():
    """ctx 是 SDK 注入参数，不得出现在 inputSchema 中。"""
    import json

    tools = anyio.run(mcp_server.server.list_tools)
    tool = next(t for t in tools if t.name == "world_gen_rule_engine")
    schema = tool.input_schema
    assert set(schema["properties"]) == {"description", "context"}
    assert schema["required"] == ["description"]
    assert "ctx" not in json.dumps(schema)


# ----------------------------------------------------------------------
# mod_search
# ----------------------------------------------------------------------
@responses.activate
def test_mod_search_modrinth_mocked():
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={
            "total_hits": 1,
            "hits": [
                {
                    "project_id": "AANobbMI",
                    "slug": "sodium",
                    "title": "Sodium",
                    "description": "fast",
                    "downloads": 5,
                    "follows": 1,
                    "categories": ["fabric"],
                    "author": "jellysquid3",
                }
            ],
        },
    )
    hits = mod_search(query="sodium", limit=1)
    assert hits[0]["slug"] == "sodium"


def test_mod_search_unknown_source():
    with pytest.raises(ToolError, match="未知的 source"):
        mod_search(query="x", source="planetminecraft")


def test_mod_search_curseforge_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSEFORGE_API_KEY", raising=False)
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    with pytest.raises(ToolError, match="API Key"):
        mod_search(query="x", source="curseforge")


@responses.activate
def test_mod_install_network_error_becomes_tool_error():
    # 404 -> NotFoundError -> ToolError (message reaches the AI client)
    responses.get(
        "https://api.modrinth.com/v2/project/definitely_missing",
        json={"error": "not found"},
        status=404,
    )
    # search fallback returns no hits -> original NotFoundError re-raised
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={"total_hits": 0, "hits": []},
    )
    with pytest.raises(ToolError, match="不存在"):
        mcp_server.mod_install(
            project="definitely_missing", mods_dir="./mods"
        )
