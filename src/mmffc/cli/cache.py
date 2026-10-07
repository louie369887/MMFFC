"""mmffc cache — 缓存管理。"""

from __future__ import annotations

import shutil

import click

from mmffc.cli import mmffc_command, mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.tty import require_yes


@mmffc_group()
def cache(ctx, **_kwargs) -> None:
    """缓存管理。"""


def _dir_size(path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


@cache.command("info")
def cache_info(ctx):
    """显示缓存目录信息。"""
    settings: CliContext = ctx.obj
    home = settings.config.home
    rows = []
    for name in ("cache", "backups"):
        target = home / name
        if target.exists():
            rows.append({"path": str(target), "bytes": _dir_size(target)})
        else:
            rows.append({"path": str(target), "bytes": 0})
    settings.renderer.emit(rows)


@cache.command("clean")
@click.option("--all", "clean_all", is_flag=True, help="清空所有缓存")
@click.option("--mods", "clean_mods", is_flag=True, help="清空模组下载缓存")
@click.option("--structures", "clean_structures", is_flag=True, help="清空结构缓存")
def cache_clean(ctx, clean_all, clean_mods, clean_structures):
    """清空缓存目录。"""
    settings: CliContext = ctx.obj
    home = settings.config.home

    if not (clean_all or clean_mods or clean_structures):
        raise click.UsageError("需要指定 --all / --mods / --structures")

    require_yes(settings.yes, "cache clean")

    targets = []
    if clean_all or clean_mods:
        targets.append(home / "cache" / "mods")
    if clean_all or clean_structures:
        targets.append(home / "cache" / "structures")

    removed = []
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
            removed.append(str(target))

    settings.renderer.emit({"cleaned": removed, "count": len(removed)})
