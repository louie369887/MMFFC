"""mmffc config — 配置校验与写入 (Phase 2 交付)。

FancyMenu DSL / FTB Quests JSON5-SNBT 的序列化器与 Schema 校验
将在 Phase 2 实现；当前为占位命令。
"""

from __future__ import annotations

import click

from mmffc.cli import mmffc_command, mmffc_group


@mmffc_group()
def config(ctx, **_kwargs) -> None:
    """配置校验与写入 (Phase 2)。"""


@config.command("validate")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests"]))
@click.argument("file", type=click.Path(exists=True))
@click.option("--schema", "schema_path", type=click.Path(exists=True), default=None)
def config_validate(ctx, target, file, schema_path):
    """校验配置文件 (Phase 2 实现)。"""
    raise click.ClickException("config validate 将在 Phase 2 实现")


@config.command("apply")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests"]))
@click.argument("file", type=click.Path(exists=True))
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), required=True, help="目标配置目录")
@click.option("--backup", is_flag=True, default=True)
def config_apply(ctx, target, file, dest_dir, backup):
    """校验并原子写入配置 (Phase 2 实现)。"""
    raise click.ClickException("config apply 将在 Phase 2 实现")


@config.command("get")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests"]))
@click.argument("path")
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), default=None)
def config_get(ctx, target, path, dest_dir):
    """读取配置节点 (Phase 2 实现)。"""
    raise click.ClickException("config get 将在 Phase 2 实现")


@config.command("set")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests"]))
@click.argument("path")
@click.argument("value")
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), default=None)
def config_set(ctx, target, path, value, dest_dir):
    """写入配置节点 (Phase 2 实现)。"""
    raise click.ClickException("config set 将在 Phase 2 实现")


@config.command("diff")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests"]))
@click.argument("file", type=click.Path(exists=True))
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), default=None)
def config_diff(ctx, target, file, dest_dir):
    """对比配置差异 (Phase 2 实现)。"""
    raise click.ClickException("config diff 将在 Phase 2 实现")
