"""mmffc world — MCA 编译与世界生成 (Phase 4 交付)。

Anvil 区块编译、HeightMap 计算、level.dat 生成将在 Phase 4 实现。
"""

from __future__ import annotations

import click

from mmffc.cli import mmffc_command, mmffc_group


@mmffc_group()
def world(ctx, **_kwargs) -> None:
    """MCA 编译与世界生成 (Phase 4)。"""


@world.command("init")
@click.argument("dir", type=click.Path(file_okay=False))
@click.option("--mc-version", "mc_version", required=True)
@click.option("--name", default="MMFFC World")
@click.option("--seed", type=int, default=None)
def world_init(ctx, dir, mc_version, name, seed):
    """初始化世界目录 (Phase 4 实现)。"""
    raise click.ClickException("world init 将在 Phase 4 实现")


@world.command("compile")
@click.option("--input", "input", type=click.Path(), default=None, help="区块 NDJSON，- 表示 stdin")
@click.option("--output", "output", type=click.Path(file_okay=False), required=True)
@click.option("--region", default=None, help="区域坐标 X,Z")
@click.option("--heightmap", default="auto")
def world_compile(ctx, input, output, region, heightmap):
    """编译区块数据为 .mca (Phase 4 实现)。"""
    raise click.ClickException("world compile 将在 Phase 4 实现")


@world.command("build")
@click.option("--rules", "rules", type=click.Path(exists=True), required=True)
@click.option("--output", "output", type=click.Path(file_okay=False), required=True)
def world_build(ctx, rules, output):
    """按规则文件生成世界 (Phase 4 实现)。"""
    raise click.ClickException("world build 将在 Phase 4 实现")


@world.command("validate")
@click.argument("dir", type=click.Path(file_okay=False))
def world_validate(ctx, dir):
    """校验世界存档完整性 (Phase 4 实现)。"""
    raise click.ClickException("world validate 将在 Phase 4 实现")


@world.command("inspect")
@click.argument("dir", type=click.Path(file_okay=False))
def world_inspect(ctx, dir):
    """检查世界存档 (Phase 4 实现)。"""
    raise click.ClickException("world inspect 将在 Phase 4 实现")
