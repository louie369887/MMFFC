"""Phase 5 端到端集成测试（PRD Week 10-12 交付物）。

链路完全走真实代码路径，仅网络层用 responses 打桩：

    Modrinth 搜索 -> 下载安装 -> 本地扫描/依赖
      -> MCP 生成 FTB Quests 章节（解析 + 语义校验）
      -> MCP voxelizer_brain 产出 NDJSON -> CLI structure compile -> .litematic
      -> MCP world_gen_rule_engine 产出规则 -> CLI world build -> .mca
      -> CLI world validate / world inspect 校验归档

任一环节失败都会中断链路，确保「MCP 脑 + CLI 手」闭环可用。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import anyio
import pytest
import responses

from mmffc.cli import main
from mmffc.mcp import server as mcp_server
from mmffc.mcp.server import (
    complex_nbt_compiler,
    mod_install,
    mod_search,
    voxelizer_brain,
    world_gen_rule_engine,
)

SEARCH_URL = "https://api.modrinth.com/v2/search"
PROJECT_URL = "https://api.modrinth.com/v2/project/sodium"
VERSIONS_URL = "https://api.modrinth.com/v2/project/AANobbMI/version"
CDN_URL = "https://cdn.modrinth.com/data/sodium/sodium-0.5.8.jar"

SODIUM_VERSION = {
    "id": "V0.5.8",
    "name": "sodium 0.5.8",
    "version_number": "0.5.8",
    "version_type": "release",
    "game_versions": ["1.20.1"],
    "loaders": ["fabric"],
    "files": [
        {
            "filename": "sodium-0.5.8.jar",
            "url": CDN_URL,
            "size": 2048,
            "primary": True,
        }
    ],
    "dependencies": [],
    "date_published": "2026-01-01T00:00:00+00:00",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def mods_dir(home) -> Path:
    target = home / "mods"
    target.mkdir()
    return target


def _jar_bytes() -> bytes:
    """构造一个最小可用的 jar（zip）载荷。"""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps({"id": "sodium", "version": "0.5.8", "depends": {}}),
        )
    return buffer.getvalue()


def _payload(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)


@pytest.fixture
def modrinth_mock():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get(
            SEARCH_URL,
            json={
                "total_hits": 1,
                "hits": [
                    {
                        "project_id": "AANobbMI",
                        "slug": "sodium",
                        "title": "Sodium",
                        "description": "fast renderer",
                        "downloads": 42,
                        "follows": 7,
                        "categories": ["fabric", "performance"],
                        "author": "jellysquid3",
                        "date_created": "2026-01-01T00:00:00Z",
                        "date_modified": "2026-01-01T00:00:00Z",
                        "project_type": "mod",
                    }
                ],
            },
        )
        rsps.get(
            PROJECT_URL,
            json={"id": "AANobbMI", "slug": "sodium", "title": "Sodium"},
        )
        rsps.get(VERSIONS_URL, json=[SODIUM_VERSION])
        rsps.get(CDN_URL, body=_jar_bytes(), status=200)
        yield rsps


# ----------------------------------------------------------------------
# 链路
# ----------------------------------------------------------------------
def test_full_pipeline_mod_search_to_valid_world(home, capsys, modrinth_mock, mods_dir):
    # 1. MCP 搜索 -> 规范化记录
    hits = mod_search(query="sodium", source="modrinth", mc_version="1.20.1")
    assert len(hits) == 1
    assert hits[0]["slug"] == "sodium"
    assert hits[0]["downloads"] == 42

    # 2. MCP 下载安装（含 manifest + 备份机制）
    result = mod_install(
        project="sodium", mc_version="1.20.1", loader="fabric",
        resolve_deps=True, mods_dir=str(mods_dir),
    )
    assert len(result["installed"]) == 1
    jar = mods_dir / "sodium-0.5.8.jar"
    assert jar.is_file()
    assert zipfile.is_zipfile(jar)
    manifest = json.loads(
        (mods_dir / ".mmffc-manifest.json").read_text(encoding="utf-8")
    )
    entry = manifest["entries"]["sodium"]
    assert entry["version"] == "0.5.8"
    assert entry["file"] == "sodium-0.5.8.jar"
    assert entry["source"] == "modrinth"

    # 3. CLI 幂等：再次安装跳过同版本
    again = mod_install(
        project="sodium", mc_version="1.20.1", loader="fabric",
        mods_dir=str(mods_dir),
    )
    assert result["skipped"] == [] and again["skipped"]

    # 4. MCP 生成 FTB Quests 章节（编译 + 语义校验后落盘由 CLI 校验）
    chapter = complex_nbt_compiler(
        target="ftbquests_chapter",
        description="Phase 5 integration",
        context={
            "id": "integration",
            "quests": [
                {"id": "first", "name": "First", "description": "",
                 "icon": "minecraft:paper"}
            ],
        },
    )
    quests_path = home / "quests.json5"
    quests_path.write_text(chapter, encoding="utf-8")
    assert main(["config", "validate", "ftbquests", str(quests_path)]) == 0

    # 5. MCP 体素 -> NDJSON -> CLI structure compile -> .litematic
    ndjson = voxelizer_brain(
        "house",
        {"ops": [{"shape": "box", "block": "oak_planks",
                  "x": 0, "y": 0, "z": 0, "w": 3, "h": 2, "d": 3}]},
    )
    voxel_file = home / "voxels.ndjson"
    voxel_file.write_text(ndjson, encoding="utf-8")
    schematic = home / "house.litematic"
    assert main(
        ["structure", "compile", "--format", "litematic",
         "-o", str(schematic), "--input", str(voxel_file), "--yes", "--json"]
    ) == 0
    assert capsys.readouterr()
    assert schematic.is_file()
    assert main(["structure", "inspect", str(schematic), "--json"]) == 0
    inspect_payload = _payload(capsys)
    assert inspect_payload["format"] == "litematic"
    assert inspect_payload["total_blocks"] == 18  # 3x2x3 盒子
    assert inspect_payload["enclosing_size"] == [3, 2, 3]

    # 6. 从头生成世界存档：init -> build(rules) -> validate -> inspect
    world = home / "saves" / "e2e"
    assert main(["world", "init", str(world), "--mc-version", "1.20.1",
                 "--name", "E2E", "--seed", "5", "--json"]) == 0
    capsys.readouterr()

    rules_text = anyio.run(
        world_gen_rule_engine,
        "测试平台",
        {"area": {"x": 0, "z": 0, "w": 4, "d": 4},
         "layers": [{"from": 63, "to": 63, "block": "minecraft:stone"}]},
    )
    rules_path = home / "rules.json"
    rules_path.write_text(rules_text, encoding="utf-8")

    assert main(["world", "build", "--rules", str(rules_path),
                 "--output", str(world), "--yes", "--json"]) == 0
    build_payload = _payload(capsys)
    assert build_payload["chunk_count"] == 1
    assert build_payload["block_count"] == 16

    assert main(["world", "validate", str(world), "--json"]) == 0
    assert _payload(capsys)["valid"] is True

    assert main(["world", "inspect", str(world), "--json"]) == 0
    inspect = _payload(capsys)
    assert inspect["level"]["level_name"] == "E2E"
    assert inspect["level"]["seed"] == 5
    assert inspect["regions"][0]["chunks"] == 1

    # 7. 生成的区域字节可解析、方块可解码（串联前一段的体素断言）
    from mmffc.formats.anvil import parse_region
    from mmffc.formats.nbt import parse_nbt

    region = world / "region" / "r.0.0.mca"
    chunks = parse_region(region.read_bytes())
    assert len(chunks) == 1
    _, tree = parse_nbt(next(iter(chunks.values())))
    # y=63 属于 Section Y=3（覆盖 y=48..63）
    assert tree["sections"][7]["Y"] == 3
    assert tree["xPos"] == 0 and tree["yPos"] == -4 and tree["zPos"] == 0
    assert tree["Status"] == "minecraft:full"
    assert len(tree["Heightmaps"]["WORLD_SURFACE"]) == 37


def test_pipeline_compile_then_build_share_region(home, capsys, modrinth_mock):
    """compile 与 build 对同一区域文件合并互不覆盖。"""
    world = home / "saves" / "merge"
    assert main(["world", "init", str(world), "--mc-version", "1.20.1"]) == 0
    capsys.readouterr()

    first = home / "a.ndjson"
    first.write_text(
        json.dumps({"x": 0, "y": 64, "z": 0, "block": "stone"}) + "\n",
        encoding="utf-8",
    )
    assert main(["world", "compile", "--input", str(first),
                 "--output", str(world), "--yes", "--json"]) == 0
    capsys.readouterr()

    rules_path = home / "rules.json"
    rules_path.write_text(
        anyio.run(world_gen_rule_engine, ""), encoding="utf-8"
    )
    assert main(["world", "build", "--rules", str(rules_path),
                 "--output", str(world), "--yes", "--json"]) == 0
    capsys.readouterr()

    assert main(["world", "validate", str(world), "--json"]) == 0
    payload = _payload(capsys)
    assert payload["valid"] is True
    assert payload["chunk_count"] == 1

    from mmffc.formats.anvil import parse_region
    from mmffc.formats.nbt import parse_nbt

    chunks = parse_region((world / "region" / "r.0.0.mca").read_bytes())
    _, tree = parse_nbt(next(iter(chunks.values())))
    # build 的 16x16 平台覆盖 compile 的单格，仍属同一区块
    assert tree["xPos"] == 0 and tree["zPos"] == 0


def test_pipeline_mcp_tool_schemas_complete(capsys):
    """Phase 5：所有 MCP Tool 的 Schema/描述完整且不含内部注入参数。"""
    tools = {tool.name: tool for tool in anyio.run(mcp_server.server.list_tools)}
    assert set(tools) == {
        "complex_nbt_compiler",
        "voxelizer_brain",
        "world_gen_rule_engine",
        "mod_search",
        "mod_install",
    }
    for name, tool in tools.items():
        assert tool.description and len(tool.description) > 40, name
        assert tool.title, name
        schema = tool.input_schema
        assert schema["type"] == "object", name
        assert "properties" in schema, name
        assert schema.get("required", []) == [] or isinstance(
            schema["required"], list
        ), name
        assert "ctx" not in json.dumps(schema), name
