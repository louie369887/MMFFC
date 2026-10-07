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
from mmffc.mcp.server import complex_nbt_compiler, mod_search

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
    }


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
