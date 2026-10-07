"""mmffc structure — 结构文件生成 (Phase 3 交付)。

体素 NDJSON -> .nbt / .litematic 的确定性序列化器将在 Phase 3 实现。
"""

from __future__ import annotations

import click

from mmffc.cli import mmffc_command, mmffc_group


@mmffc_group()
def structure(ctx, **_kwargs) -> None:
    """结构文件生成 (Phase 3)。"""


@structure.command("compile")
@click.option("--format", "fmt_choice", type=click.Choice(["nbt", "litematic"]), required=True)
@click.option("-o", "--output", "output", type=click.Path(), required=True)
@click.option("--input", "input", type=click.Path(), default=None, help="体素 NDJSON 文件，- 表示 stdin")
@click.option("--origin", default="0,0,0", help="原点坐标 X,Y,Z")
@click.option("--name", default=None)
def structure_compile(ctx, fmt_choice, output, input, origin, name):
    """编译体素数据为结构文件 (Phase 3 实现)。"""
    raise click.ClickException("structure compile 将在 Phase 3 实现")


@structure.command("inspect")
@click.argument("file", type=click.Path(exists=True))
def structure_inspect(ctx, file):
    """检查结构文件 (Phase 3 实现)。"""
    raise click.ClickException("structure inspect 将在 Phase 3 实现")


@structure.command("convert")
@click.argument("input", type=click.Path(exists=True))
@click.argument("output", type=click.Path())
@click.option("--format", "fmt_choice", type=click.Choice(["nbt", "litematic"]), required=True)
def structure_convert(ctx, input, output, fmt_choice):
    """转换结构文件格式 (Phase 3 实现)。"""
    raise click.ClickException("structure convert 将在 Phase 3 实现")
