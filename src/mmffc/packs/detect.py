"""资源包 / 数据包 / 光影包 / 世界存档识别与结构校验。

检测以「确定性文件存在性」为唯一依据（不猜内容）：

* 数据包：``pack.mcmeta`` + ``data/<namespace>/``
* 资源包：``pack.mcmeta`` + ``assets/<namespace>/``
* 光影包：``shaders/program/``（+ 可选 ``shaders/lib``、``shaders.properties``）
* 世界存档：``level.dat`` + ``region/``

``root`` 既可以是目录（:class:`pathlib.Path`），也可以是
``{相对路径: bytes}`` 的 zip 清单映射，便于解包前预检。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from mmffc.packs.meta import read_pack_meta

PACK_DATAPACK = "datapack"
PACK_RESOURCEPACK = "resourcepack"
PACK_SHADERPACK = "shaderpack"
PACK_WORLD = "world"
PACK_MODPACK = "modpack"
PACK_UNKNOWN = "unknown"

TYPE_LABELS = {
    PACK_DATAPACK: "数据包",
    PACK_RESOURCEPACK: "资源包",
    PACK_SHADERPACK: "光影包",
    PACK_WORLD: "世界存档",
    PACK_MODPACK: "整合包",
    PACK_UNKNOWN: "未知",
}

DATA_PACK_DIRS = (
    "loot_table", "recipe", "advancement", "tags", "function",
    "predicate", "structure", "worldgen", "damage_type", "chat_type",
    # 1.21 之前的复数形式
    "loot_tables", "recipes", "advancements", "functions", "structures",
    "predicates", "dimension", "dimension_type",
)
RESOURCE_PACK_DIRS = (
    "textures", "models", "sounds", "lang", "font", "shaders",
    "atlases", "blockstates", "texts", "particles", "equipment",
)


def _names(root) -> set[str]:
    """所有条目名（目录取盘上第一层，映射取全部前缀）。"""
    if isinstance(root, dict):
        found: set[str] = set()
        for key in root:
            found.add(key.rstrip("/"))
            parts = key.split("/")
            for index in range(1, len(parts)):
                found.add("/".join(parts[:index]))
        return {item for item in found if item}
    if isinstance(root, Path):
        if root.is_file():
            return {root.name}
        if not root.is_dir():
            return set()
        return {
            str(child.relative_to(root)).replace("\\", "/")
            for child in root.rglob("*")
        }
    return set()


def _has_dir(names: Iterable[str], dirname: str) -> bool:
    prefix = f"{dirname}/"
    return any(name.startswith(prefix) or name == dirname for name in names)


def _detect_from_names(names: set[str]) -> str:
    if "level.dat" in names and _has_dir(names, "region"):
        return PACK_WORLD
    if _has_dir(names, "shaders/program"):
        return PACK_SHADERPACK
    if "pack.mcmeta" in names:
        namespaces = [
            item[len("data/"):].split("/")[0]
            for item in sorted(names)
            if item.startswith("data/")
        ]
        if _has_dir(names, "data") and namespaces:
            return PACK_DATAPACK
        if _has_dir(names, "assets"):
            return PACK_RESOURCEPACK
    if _has_dir(names, "shaders/lib"):
        return PACK_SHADERPACK
    return PACK_UNKNOWN


def detect_pack_type(root) -> str:
    """识别内容类型（目录或 zip 清单映射）。"""
    return _detect_from_names(_names(root))


def inspect_pack(root, *, pack_type: str | None = None) -> dict[str, Any]:
    """结构化检查（供 pack validate / pack install 预检）。"""
    names = _names(root)
    kind = pack_type or detect_pack_type(root)
    problems: list[str] = []
    info: dict[str, Any] = {
        "type": kind,
        "type_label": TYPE_LABELS.get(kind, kind),
    }

    if kind == PACK_DATAPACK:
        meta = read_pack_meta(root)
        if not meta.found:
            problems.append("缺少 pack.mcmeta")
        else:
            problems.extend(_meta_problems(meta))
        if not _has_dir(names, "data"):
            problems.append("缺少 data/ 目录（数据包根）")
    elif kind == PACK_RESOURCEPACK:
        meta = read_pack_meta(root)
        if not meta.found:
            problems.append("缺少 pack.mcmeta")
        else:
            problems.extend(_meta_problems(meta))
        if not _has_dir(names, "assets"):
            problems.append("缺少 assets/ 目录（资源包根）")
    elif kind == PACK_SHADERPACK:
        if not _has_dir(names, "shaders/program"):
            problems.append("缺少 shaders/program/ 目录（光影包根）")
        if not any(name.endswith((".vsh", ".fsh", ".gsh")) for name in names):
            problems.append("shaders/program/ 下没有着色器文件")
    elif kind == PACK_WORLD:
        if "level.dat" not in names:
            problems.append("缺少 level.dat")
        if not _has_dir(names, "region"):
            problems.append("缺少 region/ 目录")
    else:
        problems.append(
            "无法识别内容类型（既非资源包/数据包/光影包，也非世界存档）"
        )

    info["problems"] = problems
    info["valid"] = not problems
    if kind in (PACK_DATAPACK, PACK_RESOURCEPACK):
        meta = read_pack_meta(root)
        if meta.found or meta.error:
            info["meta"] = meta.to_dict()
    return info


def _meta_problems(meta) -> list[str]:
    if meta.error:
        return [f"pack.mcmeta: {meta.error}"]
    if not meta.compatible_with_target:
        from mmffc.packs.meta import describe_format

        return [
            "pack.mcmeta: pack_format {0}（{1}）与目标 1.20.1（15）不兼容".format(
                meta.pack_format, describe_format(meta.pack_format)
            )
        ]
    return []
