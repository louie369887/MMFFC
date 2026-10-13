"""mmffc pack — 资源包 / 数据包 / 光影包 / 整合包（Phase 7，CLI.md 3.9）。

平台能力（PRD Phase 7）：

* Modrinth：``project_type`` facet 仅 mod/modpack/resourcepack/shader；
  datapack 走 ``all_project_types:datapack``；无 world 类型
* CurseForge：classId 分类（12 资源包 / 6552 光影包 / 17 世界 /
  4471 整合包 / 6945 数据包），需 CURSEFORGE_API_KEY

落盘：资源包 → ``resourcepacks/``，光影包 → ``shaderpacks/``，
数据包 → ``saves/<world>/datapacks/``（未指定世界时退回
``resourcepacks/``）；zip 解包前做类型校验与安全筛查。
"""

from __future__ import annotations

from pathlib import Path

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import SchemaError, UsageError
from mmffc.core.tty import require_yes
from mmffc.packs.install import install_release, list_installed, resolve_release
from mmffc.packs.platforms import (
    MODRINTH,
    TYPE_CHOICES,
    TYPE_LABELS,
    normalize_pack_type,
)


@mmffc_group()
def pack(ctx, **_kwargs) -> None:
    """非模组内容：资源包 / 数据包 / 光影包 / 整合包。"""


def _game_dir(value: str | None) -> Path:
    if value is None:
        raise UsageError("--game-dir 为必填（指向 .minecraft / 实例目录）")
    path = Path(value).expanduser()
    if not path.is_dir():
        raise UsageError(f"游戏目录不存在: {path}")
    return path


@pack.command("search")
@click.argument("query", default="")
@click.option("--type", "kind", required=True,
              help=f"内容类型（{', '.join(TYPE_CHOICES)}）")
@click.option("--source", type=click.Choice(["modrinth", "curseforge"]),
              default=MODRINTH)
@click.option("--mc-version", default="1.20.1")
@click.option("--limit", type=int, default=20)
@click.option("--game-dir", default=None)
def pack_search(ctx, query, kind, source, mc_version, limit, game_dir):
    """搜索资源/数据/光影包（--json 输出项目列表）。"""
    from mmffc.packs.install import search_packs

    settings: CliContext = ctx.obj
    kind = normalize_pack_type(kind)
    hits = search_packs(
        query, kind=kind, source=source, mc_version=mc_version, limit=limit
    )
    settings.renderer.emit({
        "ok": True,
        "kind": kind,
        "kind_label": TYPE_LABELS[kind],
        "source": source,
        "mc_version": mc_version,
        "count": len(hits),
        "results": [hit.to_dict() for hit in hits],
    })


@pack.command("install")
@click.argument("project")
@click.option("--type", "kind", required=True,
              help=f"内容类型（{', '.join(TYPE_CHOICES)}）")
@click.option("--source", type=click.Choice(["modrinth", "curseforge"]),
              default=MODRINTH)
@click.option("--mc-version", default="1.20.1")
@click.option("--version", "version_number", default=None, help="精确版本号")
@click.option("--game-dir", required=True, help="目标实例目录")
@click.option("--world", default=None, help="数据包目标世界名（saves/<world>/datapacks/）")
def pack_install(ctx, project, kind, source, mc_version, version_number,
                 game_dir, world):
    """下载并安装内容（资源包/数据包/光影包/世界）。"""
    settings: CliContext = ctx.obj
    kind = normalize_pack_type(kind)
    target = _game_dir(game_dir)

    release = resolve_release(
        project, kind=kind, source=source, mc_version=mc_version,
        version_number=version_number,
    )
    if settings.dry_run:
        payload = install_release(
            release, target, world=world, dry_run=True,
            backup_root=settings.config.backups_dir,
        )
        settings.renderer.emit(payload)
        return

    require_yes(settings.yes, f"pack install {project}")
    payload = install_release(
        release, target, world=world, dry_run=False,
        backup_root=settings.config.backups_dir,
    )
    settings.renderer.emit(payload)


@pack.command("list")
@click.option("--game-dir", required=True)
@click.option("--type", "kind", default=None,
              help=f"过滤类型（{', '.join(TYPE_CHOICES)}）")
def pack_list(ctx, game_dir, kind):
    """列出已安装的非模组内容。"""
    settings: CliContext = ctx.obj
    target = _game_dir(game_dir)
    items = list_installed(target, kind=kind)
    settings.renderer.emit({
        "ok": True,
        "game_dir": str(target),
        "count": len(items),
        "items": items,
    })


@pack.command("validate")
@click.argument("dir", type=click.Path(exists=True, file_okay=False))
@click.option("--type", "kind", default=None,
              help="指定类型（默认自动识别）")
def pack_validate(ctx, dir, kind):
    """校验资源包/数据包/光影包结构（pack.mcmeta / pack_format）。"""
    from mmffc.packs.detect import inspect_pack

    settings: CliContext = ctx.obj
    target = Path(dir).expanduser()
    info = inspect_pack(target, pack_type=kind)
    settings.renderer.emit({
        "ok": True,
        "dir": str(target),
        "detected": info["type"],
        "detected_label": info["type_label"],
        "problems": info["problems"],
        "valid": info["valid"],
        **({"meta": info["meta"]} if "meta" in info else {}),
    })
    if not info["valid"]:
        raise SchemaError("内容校验失败: " + "; ".join(info["problems"]))
