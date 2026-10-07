"""MMFFC MCP Server — AI 高阶模块（"脑"）。

通过 MCP (Model Context Protocol) 暴露工具，由外部
AI Agent（Claude Desktop / Cursor 等）调用。MCP 使用
JSON-RPC 消息格式，工具通过 ``tools/list`` 发现，
通过 ``tools/call`` 调用。

边界（CLI.md 第 6 节）：

* MCP 负责"脑"：生成 JSON/NDJSON/规则文件内容
* CLI 负责"手"：通过 stdin 接收 AI 输出，做
  Schema 校验、语义校验、原子写入
* 本 Server 与 CLI 共享同一套核心模块
  (mod_manager / file_io_engine)

工具清单：

* ``complex_nbt_compiler`` — 自然语言描述 -> FancyMenu
  布局 DSL / FTB Quests 任务树（JSON5 或 SNBT）
* ``mod_search`` — Modrinth / CurseForge 模组搜索
* ``mod_install`` — 模组安装（确定性，含备份）
* ``voxelizer_brain`` — 形状/方块描述 -> 体素 NDJSON
  （由 CLI ``structure compile`` 落盘为 .nbt / .litematic）
"""

from __future__ import annotations

import functools
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mmffc import __version__
from mmffc.core.config import load_config
from mmffc.core.errors import MMFFCError, SchemaError
from mmffc.formats import fancymenu as fancymenu_format
from mmffc.formats import ftbquests as ftbquests_format
from mmffc.io.file_io import FileIOEngine
from mmffc.mods.curseforge import LOADER_TYPES, CurseForgeClient
from mmffc.mods.modrinth import ModrinthClient, normalize_hit
from mmffc.mods.registry import execute_install, plan_install

MCP_SERVER_NAME = "mmffc"

server = MCPServer(
    MCP_SERVER_NAME,
    version=__version__,
)


def _config():
    return load_config()


def _anticipated_errors(fn):
    """Convert MMFFCError into ToolError.

    ToolError surfaces the message to the AI client as an
    is_error result; any other exception is treated as a
    crash and the client only sees 'Error executing tool'.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MMFFCError as exc:
            raise ToolError(exc.message) from exc

    return wrapper


def _engine() -> FileIOEngine:
    config = _config()
    return FileIOEngine(config.backups_dir)


# ----------------------------------------------------------------------
# complex_nbt_compiler
# ----------------------------------------------------------------------
@server.tool(
    name="complex_nbt_compiler",
    title="Complex NBT/Config Compiler",
    description=(
        "将自然语言描述转化为符合 FancyMenu 或 FTB Quests "
        "格式规范的配置文件内容（确定性编译 + 两阶段校验）。\n\n"
        "target 取值：\n"
        "- fancymenu_layout: FancyMenu 布局 DSL 文本\n"
        "- ftbquests_chapter: FTB Quests 章节（JSON5 或 SNBT）\n\n"
        "description: 自然语言描述，同时作为布局名称/章节名称的"
        "默认来源。\n\n"
        "context: 可选的结构化内容（由 AI 根据用户意图填充）：\n"
        "- fancymenu_layout: {layout_meta: {key: value}, "
        "elements: [{type, id, x, y, width, height, ...属性}]}\n"
        "  元素 type 必须是 button/text/image/texture/rectangle/"
        "item/scrollable/tooltip/sound/empty 之一。\n"
        "- ftbquests_chapter: {id, name, description, icon, "
        "format: 'json5'|'snbt', quests: [{id, name, description, "
        "icon, tasks, rewards, dependencies}]}；任务 id 必须匹配 "
        "^[a-z0-9_.-]+$，且 id/name/description/icon 为必需字段。\n\n"
        "返回：经过 Schema 校验的目标格式字符串。"
        "落盘请使用 mmffc config apply（CLI）。"
    ),
    )
@_anticipated_errors
def complex_nbt_compiler(
    target: str,
    description: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    """Compile a natural-language description into a
    validated FancyMenu layout or FTB Quests chapter."""
    context = context or {}

    if target == "fancymenu_layout":
        document = _build_fancymenu_layout(description, context)
        content = fancymenu_format.serialize_layout(document)
        errors = fancymenu_format.validate_layout(document)
    elif target == "ftbquests_chapter":
        fmt = context.get("format", "json5")
        document = _build_ftbquests_chapter(description, context)
        content = ftbquests_format.serialize_chapter(
            document, fmt=fmt
        )
        errors = ftbquests_format.validate_chapter(document)
    else:
        raise SchemaError(
            f"未知的 target: {target!r} "
            f"(可选: fancymenu_layout, ftbquests_chapter)"
        )

    errors = [e for e in errors if not e.startswith("警告: ")]
    if errors:
        raise SchemaError(
            "编译结果未通过语义校验: " + "; ".join(errors)
        )
    return content


def _build_fancymenu_layout(
    description: str, context: dict[str, Any]
) -> Any:
    """Deterministically build a LayoutDocument."""
    layout_meta = {
        str(key): _format_value(value)
        for key, value in (context.get("layout_meta") or {}).items()
    }
    layout_meta.setdefault("name", description or "mmffc_layout")

    sections = [fancymenu_format.Section("layout-meta", layout_meta)]

    background = context.get("background")
    if isinstance(background, dict):
        sections.append(
            fancymenu_format.Section(
                "menu_background",
                {str(k): str(v) for k, v in background.items()},
            )
        )

    elements = context.get("elements")
    if not isinstance(elements, list) or not elements:
        # 默认布局：居中文本元素展示描述
        elements = [
            {
                "type": "text",
                "id": "mmffc_title",
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 20,
                "anchor": "center",
                "text": description or "MMFFC Layout",
            }
        ]

    for element in elements:
        if not isinstance(element, dict):
            raise SchemaError("elements 必须是对象数组")
        element_type = element.get("type")
        element_id = element.get("id")
        if element_type not in fancymenu_format.ELEMENT_TYPES:
            raise SchemaError(
                f"未知元素类型: {element_type!r}"
            )
        if not element_id or not fancymenu_format.IDENTIFIER_RE.match(
            str(element_id)
        ):
            raise SchemaError(
                f"非法元素标识符: {element_id!r}"
            )
        properties = {
            str(key): _format_value(value)
            for key, value in element.items()
            if key not in ("type", "id")
        }
        sections.append(
            fancymenu_format.Section(
                f"element:{element_type}:{element_id}",
                properties,
            )
        )

    return fancymenu_format.LayoutDocument(
        doc_type=fancymenu_format.DOC_TYPE,
        sections=sections,
    )


def _build_ftbquests_chapter(
    description: str, context: dict[str, Any]
) -> dict[str, Any]:
    """Deterministically build a chapter dict."""
    chapter: dict[str, Any] = {}

    chapter_id = context.get("id")
    if chapter_id is not None:
        chapter["id"] = chapter_id
    chapter["name"] = context.get("name") or description or "MMFFC Chapter"
    chapter["description"] = context.get("description") or ""
    chapter["icon"] = context.get("icon") or "minecraft:book"

    quests = context.get("quests")
    if not isinstance(quests, list) or not quests:
        quest_id = _slugify(description or "first_quest")
        quests = [
            {
                "id": quest_id,
                "name": description or "First Quest",
                "description": "",
                "icon": "minecraft:paper",
                "tasks": [],
                "rewards": [],
                "dependencies": [],
            }
        ]

    normalized_quests = []
    for index, quest in enumerate(quests):
        if not isinstance(quest, dict):
            raise SchemaError("quests 必须是对象数组")
        normalized = dict(quest)
        if not normalized.get("id"):
            normalized["id"] = _slugify(
                normalized.get("name") or f"quest_{index}"
            )
        normalized.setdefault("description", "")
        normalized.setdefault("icon", "minecraft:paper")
        normalized_quests.append(normalized)
    chapter["quests"] = normalized_quests

    return chapter


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _slugify(text: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9_.-]+", "_", text.lower()).strip("_")
    return slug or "unnamed"


# ----------------------------------------------------------------------
# mod_search / mod_install
# ----------------------------------------------------------------------
@server.tool(
    name="mod_search",
    title="Mod Search",
    description=(
        "在 Modrinth（默认）或 CurseForge 上搜索 Minecraft "
        "模组。返回 project_id、slug、标题、描述、下载量等字段。\n\n"
        "参数：\n"
        "- query: 搜索关键词（可选）\n"
        "- source: 'modrinth'（默认）或 'curseforge'"
        "（CurseForge 需要 CURSEFORGE_API_KEY）\n"
        "- loader: 'fabric' | 'forge' | 'quilt' | 'neoforge'（可选）\n"
        "- mc_version: 游戏版本过滤，如 '1.20.1'（可选）\n"
        "- limit: 结果数量上限（默认 20）\n\n"
        "只读操作，无副作用。"
    ),
)
@_anticipated_errors
def mod_search(
    query: str = "",
    source: str = "modrinth",
    loader: str | None = None,
    mc_version: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search Modrinth / CurseForge for mods."""
    if source == "modrinth":
        facets = ["project_type:mod"]
        if loader:
            facets.append([f"loaders:{loader}"])
        if mc_version:
            facets.append([f"versions:{mc_version}"])
        client = ModrinthClient(token=_config().modrinth_token)
        result = client.search(
            query, facets=facets, limit=limit
        )
        return [normalize_hit(hit) for hit in result.get("hits", [])]

    if source == "curseforge":
        api_key = _config().curseforge_api_key
        if not api_key:
            raise SchemaError(
                "CurseForge 需要 API Key（设置 CURSEFORGE_API_KEY "
                "或写入配置文件）"
            )
        client = CurseForgeClient(api_key=api_key)
        result = client.search_mods(
            query or None,
            game_version=mc_version,
            mod_loader=loader,
            page_size=min(limit, 50),
        )
        from mmffc.mods.curseforge import normalize_hit as cf_normalize

        return [
            cf_normalize(entry)
            for entry in result.get("data", [])
        ]

    raise SchemaError(f"未知的 source: {source!r}")


@server.tool(
    name="mod_install",
    title="Mod Install",
    description=(
        "从 Modrinth 下载并安装模组到 mods 目录（原子写入 + "
        "自动备份）。\n\n"
        "参数：\n"
        "- project: Modrinth project_id 或 slug，如 'sodium'\n"
        "- mc_version: 目标游戏版本，如 '1.20.1'（可选）\n"
        "- loader: 'fabric' | 'forge' | 'quilt' | 'neoforge'（可选）\n"
        "- version_number: 精确版本号（可选，默认选最新 release）\n"
        "- resolve_deps: 是否递归安装必需依赖（默认 false）\n"
        "- mods_dir: 目标 mods 目录（默认配置中的 mods_dir）\n\n"
        "返回安装结果摘要。幂等：已安装的精确版本会跳过；"
        "版本冲突时旧文件自动备份后覆盖。"
    ),
)
@_anticipated_errors
def mod_install(
    project: str,
    mc_version: str | None = None,
    loader: str | None = None,
    version_number: str | None = None,
    resolve_deps: bool = False,
    mods_dir: str | None = None,
) -> dict[str, Any]:
    """Install a mod from Modrinth with dependency resolution."""
    config = _config()
    target_dir = (
        os.path.expanduser(mods_dir)
        if mods_dir
        else str(config.mods_dir)
    )
    from pathlib import Path

    mods_path = Path(target_dir)
    client = ModrinthClient(token=config.modrinth_token)
    plan = plan_install(
        client,
        project,
        version_number=version_number,
        mc_version=mc_version,
        loader=loader,
        resolve_deps=resolve_deps,
        mods_dir=mods_path,
    )
    if plan.conflicts:
        # 冲突时强制覆盖并备份（调用方即 AI Agent，
        # 备份机制保证可回滚）
        force = True
    else:
        force = False
    results = execute_install(
        client,
        plan,
        mods_path,
        backup_root=config.backups_dir,
        force=force,
    )
    return {
        "installed": results,
        "skipped": plan.skipped,
        "conflicts": plan.conflicts,
    }


# ----------------------------------------------------------------------
# voxelizer_brain
# ----------------------------------------------------------------------
@server.tool(
    name="voxelizer_brain",
    title="Voxelizer Brain",
    description=(
        "将形状指令/方块列表编译为体素 NDJSON（每行一个方块："
        '{"x":0,"y":0,"z":0,"block":"minecraft:stone"}）。\n\n'
        "参数：\n"
        "- description: 自然语言描述（无 ops/blocks 时生成 5x5 石头"
        "平台占位，并透传给后续命名使用）\n"
        "- context: {ops: [...], blocks: [...]}（均可选）\n\n"
        "ops 按顺序绘制（后者覆盖前者），每个 op 需要 shape 与 block"
        "（默认 minecraft:stone），可选 properties；shape 参数：\n"
        "- box / hollow_box: x,y,z,w,h,d（hollow_box 可选 "
        "thickness，默认 1）\n"
        "- sphere: cx,cy,cz,r\n"
        "- hollow_sphere: cx,cy,cz,r,thickness（默认 1）\n"
        "- cylinder: cx,cy,cz,r,h,axis（默认 y，底面中心沿 +axis 延伸）\n"
        "- line: from:[x,y,z],to:[x,y,z]\n\n"
        "blocks 为显式体素数组（同 NDJSON 字段），作为最顶层覆盖。\n\n"
        "返回：校验后的 NDJSON 字符串。落盘请接 CLI：\n"
        "cat out.ndjson | mmffc structure compile --format litematic "
        "-o out.litematic"
    ),
)
@_anticipated_errors
def voxelizer_brain(
    description: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Compile shape/block instructions into validated voxel NDJSON."""
    from mmffc.core.voxels import dumps_ndjson, generate, parse_ndjson

    voxels = generate(description, context)
    text = dumps_ndjson(voxels)
    parse_ndjson(text)  # 输出前自校验 roundtrip
    return text


def run(transport: str = "stdio") -> None:
    """Run the MCP server."""
    server.run(transport=transport)


def main() -> int:
    """Entry point for the mmffc-mcp console script."""
    try:
        run()
        return 0
    except MMFFCError as exc:
        import sys

        print(f"mmffc-mcp: 错误: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
