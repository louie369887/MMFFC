"""内容类型 <-> 平台能力栅格与安装规划（Phase 7）。

平台能力（已调研确认）：

* Modrinth v2：search 的 ``project_type`` facet 仅支持
  ``mod / modpack / resourcepack / shader``；``datapack`` 只能经
  ``all_project_types:datapack`` 过滤；**无 world 项目类型**
* CurseForge v1（gameId=432）：classId 分类——6 模组 / 12 资源包 /
  6552 光影包 / 17 世界 / 4471 整合包 / 6945 数据包
* MinecraftMap：无公开 API，不在此实现（HTML 抓取脆性高）

安装目标目录：资源包 → ``resourcepacks/``，光影包 → ``shaderpacks/``，
世界存档 → ``saves/``，数据包 → ``saves/<world>/datapacks/``
（若未指定世界则退回 ``resourcepacks/``，与 CurseForge/Prism 行为一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mmffc.core.errors import SchemaError

MODRINTH = "modrinth"
CURSEFORGE = "curseforge"
SOURCE_CHOICES = (MODRINTH, CURSEFORGE)

DATAPACK = "datapack"
RESOURCEPACK = "resourcepack"
SHADERPACK = "shaderpack"
WORLD = "world"
MODPACK = "modpack"
MOD = "mod"

TYPE_CHOICES = (RESOURCEPACK, DATAPACK, SHADERPACK, WORLD, MODPACK)
TYPE_LABELS = {
    RESOURCEPACK: "资源包",
    DATAPACK: "数据包",
    SHADERPACK: "光影包",
    WORLD: "世界存档",
    MODPACK: "整合包",
    MOD: "模组",
}

# Modrinth project_type（search facet 可用值）
MODRINTH_PROJECT_TYPES = {
    RESOURCEPACK: "resourcepack",
    SHADERPACK: "shader",
    MODPACK: "modpack",
    MOD: "mod",
}
# datapack 在 search facet 中不存在，必须走 all_project_types
MODRINTH_FALLBACK_FACETS = {DATAPACK: ("all_project_types:datapack",)}

# CurseForge classId
CURSEFORGE_CLASS_IDS = {
    MOD: 6,
    RESOURCEPACK: 12,
    SHADERPACK: 6552,
    WORLD: 17,
    MODPACK: 4471,
    DATAPACK: 6945,
}

INSTALL_DIRS = {
    RESOURCEPACK: "resourcepacks",
    SHADERPACK: "shaderpacks",
    WORLD: "saves",
    DATAPACK: "saves/<world>/datapacks",
    MODPACK: "mods",
}


def normalize_pack_type(value: str) -> str:
    if value not in TYPE_CHOICES:
        allowed = ", ".join(TYPE_CHOICES)
        raise SchemaError(f"未知内容类型 {value!r}（可选: {allowed}）")
    return value


def install_target(kind: str, game_dir: Path, *, world: str | None = None) -> Path:
    """内容类型 -> 安装目录。"""
    kind = normalize_pack_type(kind)
    if kind == DATAPACK:
        if world:
            return game_dir / "saves" / world / "datapacks"
        # 未指定世界：装到 resourcepacks/（Global Packs 兼容行为）
        return game_dir / "resourcepacks"
    return game_dir / INSTALL_DIRS[kind]


def modrinth_facets(kind: str, mc_version: str | None) -> list[list[str]]:
    """Modrinth search facets（AND of ORs）。"""
    kind = normalize_pack_type(kind)
    project_type = MODRINTH_PROJECT_TYPES.get(kind)
    if project_type is None:
        facets = [[MODRINTH_FALLBACK_FACETS[kind][0]]]
    else:
        facets = [[f"project_type:{project_type}"]]
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    return facets


def curseforge_class_id(kind: str) -> int:
    kind = normalize_pack_type(kind)
    return CURSEFORGE_CLASS_IDS[kind]


@dataclass
class PackCandidate:
    """一次搜索结果（跨平台统一 schema）。"""

    source: str
    project_id: str
    slug: str
    title: str
    description: str
    kind: str
    downloads: int | None = None
    version_id: str | None = None
    version_number: str | None = None
    file_name: str | None = None
    file_url: str | None = None
    file_size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "project_id": self.project_id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "kind_label": TYPE_LABELS.get(self.kind, self.kind),
            "downloads": self.downloads,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "file_name": self.file_name,
            "file_size": self.file_size,
        }


def from_modrinth_hit(hit: dict[str, Any], kind: str) -> PackCandidate:
    return PackCandidate(
        source=MODRINTH,
        project_id=hit.get("project_id") or hit.get("id") or "",
        slug=hit.get("slug") or "",
        title=hit.get("title") or hit.get("slug") or "",
        description=hit.get("description") or "",
        kind=kind,
        downloads=hit.get("downloads"),
    )


def from_curseforge_entry(entry: dict[str, Any], kind: str) -> PackCandidate:
    return PackCandidate(
        source=CURSEFORGE,
        project_id=str(entry.get("id") or ""),
        slug=entry.get("slug") or "",
        title=entry.get("name") or "",
        description=entry.get("summary") or "",
        kind=kind,
        downloads=entry.get("downloadCount"),
    )
