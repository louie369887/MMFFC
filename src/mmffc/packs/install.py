"""资源包/数据包/光影包/世界的下载与落盘（Phase 7）。

下载走既有客户端（ModrinthClient / CurseForgeClient），
落盘策略：zip 解包到目标目录（防 zip-slip / 炸弹），单文件直接拷贝；
世界存档复用 :func:`mmffc.packs.archive.import_world`。

写入沿用原子写入 + 备份 + ``--dry-run`` + ``--yes`` 契约，
清单记录到目标目录的 ``.mmffc-manifest.json``。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mmffc.core.errors import NetworkError, SchemaError, UsageError
from mmffc.packs.archive import is_archive, plan_archive
from mmffc.packs.detect import (
    PACK_WORLD,
    TYPE_LABELS,
    detect_pack_type,
    inspect_pack,
)
from mmffc.packs.platforms import (
    CURSEFORGE,
    MODRINTH,
    TYPE_CHOICES,
    WORLD,
    PackCandidate,
    curseforge_class_id,
    install_target,
    modrinth_facets,
    normalize_pack_type,
)

MANIFEST_NAME = ".mmffc-manifest.json"
PACK_EXTENSIONS = (".zip", ".mcworld")


@dataclass
class PackRelease:
    """选定的一次发布（准备下载的文件）。"""

    candidate: PackCandidate
    version_id: str
    version_number: str
    file_name: str
    file_url: str
    file_size: int | None


def _load_config():
    from mmffc.core.config import load_config

    return load_config()


def search_packs(
    query: str,
    *,
    kind: str,
    source: str = MODRINTH,
    mc_version: str | None = None,
    limit: int = 20,
) -> list[PackCandidate]:
    """跨平台搜索资源包/数据包/光影包/整合包。"""
    kind = normalize_pack_type(kind)
    if source == MODRINTH:
        from mmffc.mods.modrinth import ModrinthClient

        client = ModrinthClient(token=_load_config().modrinth_token)
        result = client.search(
            query or "",
            facets=modrinth_facets(kind, mc_version),
            limit=min(limit, 50),
        )
        return [
            PackCandidate(
                source=MODRINTH,
                project_id=hit.get("project_id") or hit.get("id") or "",
                slug=hit.get("slug") or "",
                title=hit.get("title") or "",
                description=hit.get("description") or "",
                kind=kind,
                downloads=hit.get("downloads"),
            )
            for hit in result.get("hits", [])
        ]

    if source == CURSEFORGE:
        from mmffc.mods.curseforge import CurseForgeClient

        config = _load_config()
        if not config.curseforge_api_key:
            raise SchemaError(
                "CurseForge 需要 API Key（设置 CURSEFORGE_API_KEY 或写入配置文件）"
            )
        client = CurseForgeClient(api_key=config.curseforge_api_key)
        result = client.search_mods(
            query or None,
            game_version=mc_version,
            class_id=curseforge_class_id(kind),
            page_size=min(limit, 50),
        )
        entries = result.get("data", [])
        return [
            PackCandidate(
                source=CURSEFORGE,
                project_id=str(entry.get("id") or ""),
                slug=entry.get("slug") or "",
                title=entry.get("name") or "",
                description=entry.get("summary") or "",
                kind=kind,
                downloads=entry.get("downloadCount"),
            )
            for entry in entries
        ]

    raise SchemaError(f"未知来源: {source!r}（可选: modrinth, curseforge）")


def resolve_release(
    project: str,
    *,
    kind: str,
    source: str = MODRINTH,
    mc_version: str | None = None,
    version_number: str | None = None,
) -> PackRelease:
    """定位项目 + 选版本 + 取下载文件。"""
    kind = normalize_pack_type(kind)
    if source == MODRINTH:
        from mmffc.mods.modrinth import ModrinthClient

        client = ModrinthClient(token=_load_config().modrinth_token)
        project_data = client.get_project(project)
        versions = client.get_versions(project_data["id"])
        if mc_version:
            versions = [
                v for v in versions if mc_version in (v.get("game_versions") or [])
            ]
        if version_number:
            versions = [
                v for v in versions if v.get("version_number") == version_number
            ]
        if not versions:
            raise SchemaError(
                f"未找到匹配版本: {project!r} kind={kind} mc={mc_version}"
            )
        version = ModrinthClient.sort_versions(versions)[0]
        files = version.get("files") or []
        primary = next((f for f in files if f.get("primary")), None) or (
            files[0] if files else None
        )
        if primary is None:
            raise SchemaError(f"版本没有可下载文件: {version.get('version_number')}")
        return PackRelease(
            candidate=PackCandidate(
                source=MODRINTH,
                project_id=project_data.get("id", project),
                slug=project_data.get("slug", project),
                title=project_data.get("title", project),
                description=project_data.get("description", ""),
                kind=kind,
            ),
            version_id=version.get("id", ""),
            version_number=version.get("version_number", ""),
            file_name=primary.get("filename", ""),
            file_url=primary.get("url", ""),
            file_size=primary.get("size"),
        )

    if source == CURSEFORGE:
        from mmffc.mods.curseforge import CurseForgeClient

        config = _load_config()
        if not config.curseforge_api_key:
            raise SchemaError("CurseForge 需要 API Key（CURSEFORGE_API_KEY）")
        client = CurseForgeClient(api_key=config.curseforge_api_key)
        mod_id = _curseforge_project_id(client, project, kind)
        files = client.get_mod_files(mod_id, game_version=mc_version)
        file_list = files.get("data", []) if isinstance(files, dict) else files
        if version_number:
            file_list = [
                f for f in file_list if str(f.get("id")) == version_number
                or f.get("fileName") == version_number
            ]
        if not file_list:
            raise SchemaError(
                f"未找到匹配文件: {project!r} kind={kind} mc={mc_version}"
            )
        chosen = sorted(
            file_list, key=lambda f: str(f.get("fileDate") or ""), reverse=True
        )[0]
        download_url = (
            client.get_download_url(mod_id, chosen["id"]).get("data")
            if chosen.get("id")
            else None
        )
        mod_data = client.get_mod(mod_id).get("data", {})
        return PackRelease(
            candidate=PackCandidate(
                source=CURSEFORGE,
                project_id=str(mod_id),
                slug=mod_data.get("slug", project),
                title=mod_data.get("name", project),
                description=mod_data.get("summary", ""),
                kind=kind,
                downloads=mod_data.get("downloadCount"),
            ),
            version_id=str(chosen.get("id", "")),
            version_number=str(chosen.get("displayName", chosen.get("id", ""))),
            file_name=chosen.get("fileName", ""),
            file_url=download_url or "",
            file_size=chosen.get("fileLength"),
        )

    raise SchemaError(f"未知来源: {source!r}")


def _curseforge_project_id(client, project: str, kind: str) -> int:
    """CurseForge 项目 id 解析（数字 id 或按类型搜索 slug）。"""
    if project.isdigit():
        return int(project)
    result = client.search_mods(
        project, class_id=curseforge_class_id(kind), page_size=10
    )
    for entry in result.get("data", []):
        if entry.get("slug") == project:
            return int(entry["id"])
    if result.get("data"):
        return int(result["data"][0]["id"])
    raise SchemaError(f"CurseForge 未找到项目: {project!r} (kind={kind})")


def install_release(
    release: PackRelease,
    game_dir: Path,
    *,
    world: str | None = None,
    dry_run: bool = False,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    """下载并落盘一次发布；返回操作摘要。"""
    from mmffc.io.atomic import atomic_write_bytes
    from mmffc.mods.modrinth import ModrinthClient

    kind = release.candidate.kind
    target_dir = install_target(kind, Path(game_dir), world=world)
    payload = {
        "ok": True,
        "source": release.candidate.source,
        "kind": kind,
        "kind_label": TYPE_LABELS.get(kind, kind),
        "project": release.candidate.slug or release.candidate.project_id,
        "project_id": release.candidate.project_id,
        "version": release.version_number,
        "file_name": release.file_name,
        "target": str(target_dir),
        "world": world,
        "dry_run": dry_run,
    }

    if dry_run:
        payload.update({"downloaded": None, "entry_count": None, "backup": None})
        return payload

    config = _load_config()
    client = ModrinthClient(token=config.modrinth_token)
    temp_dir = Path(tempfile.mkdtemp(prefix="mmffc-pack-"))
    try:
        downloaded = temp_dir / release.file_name
        try:
            client.download_file(release.file_url, downloaded)
        except NetworkError as exc:
            raise NetworkError(f"下载失败 {release.file_name}: {exc}") from exc

        if kind == WORLD:
            from mmffc.packs.archive import import_world

            result = import_world(downloaded, target_dir)
            payload.update(
                {
                    "world_dir": str(result["world_dir"]),
                    "entry_count": len(result["plan"]["entries"]),
                    "backup": None,
                }
            )
            return payload

        if is_archive(downloaded):
            from mmffc.packs.archive import extract_archive

            plan = plan_archive(downloaded)
            if not plan.safe:
                raise SchemaError("归档含不安全条目，拒绝解包")
            # 用「归档条目清单映射」预检内容类型，避免先落盘后校验
            staging = Path(tempfile.mkdtemp(prefix="mmffc-stage-", dir=str(temp_dir)))
            extract_archive(plan, staging)
            info = inspect_pack(staging)
            if not info["valid"]:
                raise SchemaError(
                    "内容校验失败: " + "; ".join(info["problems"])
                )
            target_dir.mkdir(parents=True, exist_ok=True)
            for child in staging.iterdir():
                shutil.move(str(child), str(target_dir / child.name))
            payload.update(
                {
                    "downloaded": release.file_name,
                    "entry_count": len(plan.entries),
                    "detected": info["type"],
                    "backup": None,
                }
            )
            _append_manifest(target_dir, release)
            return payload

        # 单文件（jar/pack 直装）
        data = downloaded.read_bytes()
        dest = target_dir / release.file_name
        backup = atomic_write_bytes(dest, data, backup_root=backup_root)
        payload.update(
            {
                "downloaded": str(downloaded.name),
                "bytes": len(data),
                "backup": str(backup) if backup else None,
            }
        )
        _append_manifest(target_dir, release)
        return payload
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _append_manifest(target_dir: Path, release: PackRelease) -> None:
    """记录安装清单（失败不影响安装结果）。"""
    path = target_dir / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    entries = data.get("entries", {})
    entries[release.candidate.slug or release.candidate.project_id] = {
        "source": release.candidate.source,
        "project_id": release.candidate.project_id,
        "kind": release.candidate.kind,
        "version": release.version_number,
        "file": release.file_name,
    }
    data["entries"] = entries
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_installed(game_dir: Path, *, kind: str | None = None) -> list[dict[str, Any]]:
    """列出已安装的资源内容（扫 manifest 与目录结构）。"""
    root = Path(game_dir)
    found: list[dict[str, Any]] = []
    for directory, expected in (
        (root / "resourcepacks", "resourcepack"),
        (root / "shaderpacks", "shaderpack"),
        (root / "saves", "world"),
    ):
        if not directory.is_dir():
            continue
        if kind and kind != expected:
            continue
        for child in sorted(directory.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            if expected == "world":
                info = detect_pack_type(child)
                if info != PACK_WORLD:
                    continue
            found.append(
                {
                    "kind": expected,
                    "kind_label": TYPE_LABELS.get(expected, expected),
                    "name": child.name,
                    "path": str(child),
                }
            )
    datapacks = root / "saves"
    if datapacks.is_dir() and (kind in (None, "datapack")):
        for world_dir in sorted(p for p in datapacks.iterdir() if p.is_dir()):
            pack_dir = world_dir / "datapacks"
            if not pack_dir.is_dir():
                continue
            for child in sorted(pack_dir.iterdir()):
                if child.name.startswith("."):
                    continue
                found.append(
                    {
                        "kind": "datapack",
                        "kind_label": TYPE_LABELS["datapack"],
                        "name": f"{world_dir.name}/{child.name}",
                        "path": str(child),
                    }
                )
    return found


__all__ = [
    "PackRelease",
    "install_release",
    "list_installed",
    "resolve_release",
    "search_packs",
]
