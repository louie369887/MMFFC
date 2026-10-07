"""mmffc structure — 结构文件生成（Phase 3，CLI.md 3.3）。

体素 NDJSON -> ``.nbt`` / ``.litematic`` 的确定性序列化：

* ``compile`` — stdin/文件读入 NDJSON，校验后原子写入（含备份）
* ``inspect``  — 检查结构文件（格式/尺寸/调色板/元数据）
* ``convert``  — 两种格式互转（经体素网格中转）

数据错误退出码 3（SchemaError），非 TTY 高风险写入缺 ``--yes`` 退出码 2。
"""

from __future__ import annotations

from pathlib import Path

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import UsageError
from mmffc.core.tty import require_yes
from mmffc.core.voxels import Grid, build_grid, parse_ndjson
from mmffc.formats.litematic import build_litematic, inspect_litematic_tree, parse_litematic_tree
from mmffc.formats.nbt import parse_nbt
from mmffc.formats.structure import (
    DEFAULT_DATA_VERSION,
    FORMAT_LITEMATIC,
    FORMAT_NBT,
    build_structure,
    detect_format,
    inspect_nbt_tree,
    parse_structure,
)
from mmffc.io.atomic import atomic_write_bytes

FORMAT_CHOICES = [FORMAT_NBT, FORMAT_LITEMATIC]


@mmffc_group()
def structure(ctx, **_kwargs) -> None:
    """结构文件生成（NDJSON -> .nbt / .litematic）。"""


def _read_input(input_path: str | None) -> str:
    if input_path is None or input_path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(input_path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UsageError(f"读取输入失败: {input_path}: {exc}") from None


def _parse_origin(origin: str | None) -> tuple[int, int, int] | None:
    if origin is None:
        return None
    parts = origin.split(",")
    if len(parts) != 3:
        raise UsageError(f"--origin 必须是 X,Y,Z 格式: {origin!r}")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        raise UsageError(f"--origin 必须是整数坐标: {origin!r}") from None


def _data_version(value: int | None) -> int:
    if value is None:
        return DEFAULT_DATA_VERSION
    if value <= 0:
        raise UsageError(f"--data-version 必须为正: {value}")
    return value


def _build_blob(
    fmt: str,
    grid: Grid,
    *,
    data_version: int,
    name: str | None,
) -> bytes:
    if fmt == FORMAT_NBT:
        return build_structure(grid, data_version=data_version)
    return build_litematic(
        grid,
        name=name or "MMFFC Structure",
        data_version=data_version,
    )


def _write_result(
    settings: CliContext,
    payload: dict,
    blob: bytes,
    output: str,
    action: str,
) -> None:
    """dry-run 直接汇报；否则 require_yes + 原子写入 + 备份。"""
    dest = Path(output).expanduser()
    if settings.dry_run:
        settings.renderer.emit({**payload, "dry_run": True, "backup": None})
        return

    require_yes(settings.yes, action)
    backup = atomic_write_bytes(
        dest, blob, backup_root=settings.config.backups_dir
    )
    settings.renderer.emit(
        {
            **payload,
            "dry_run": False,
            "path": str(dest),
            "backup": str(backup) if backup else None,
        }
    )


@structure.command("compile")
@click.option("--format", "fmt_choice", type=click.Choice(FORMAT_CHOICES), required=True)
@click.option("-o", "--output", "output", type=click.Path(), required=True, help="输出结构文件路径")
@click.option("--input", "input_path", type=click.Path(), default=None, help="体素 NDJSON 文件，- 或省略表示 stdin")
@click.option("--origin", default=None, help="原点坐标 X,Y,Z（默认贴合体素最小角）")
@click.option("--name", default=None, help="litematic Metadata.Name（默认取输出文件名）")
@click.option("--data-version", type=int, default=None, help=f"DataVersion（默认 {DEFAULT_DATA_VERSION} = 1.20.1）")
def structure_compile(ctx, fmt_choice, output, input_path, origin, name, data_version):
    """编译体素 NDJSON 为结构文件。"""
    settings: CliContext = ctx.obj

    text = _read_input(input_path)
    voxels = parse_ndjson(text)
    grid = build_grid(voxels, origin=_parse_origin(origin))
    version = _data_version(data_version)
    blob = _build_blob(
        fmt_choice,
        grid,
        data_version=version,
        name=name or Path(output).stem,
    )

    _write_result(
        settings,
        {
            "ok": True,
            "format": fmt_choice,
            "size": list(grid.size),
            "volume": grid.volume,
            "block_count": grid.block_count,
            "palette_size": len(grid.palette),
            "voxel_count": len(voxels),
            "data_version": version,
        },
        blob,
        output,
        "structure compile",
    )


@structure.command("inspect")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def structure_inspect(ctx, file):
    """检查结构文件（自动识别 nbt / litematic；--json 走全局输出格式）。"""
    settings: CliContext = ctx.obj
    try:
        raw = Path(file).read_bytes()
    except OSError as exc:
        raise UsageError(f"读取文件失败: {file}: {exc}") from None
    _, tree = parse_nbt(raw)
    fmt = detect_format(tree)
    if fmt == FORMAT_NBT:
        info = inspect_nbt_tree(tree)
    else:
        info = inspect_litematic_tree(tree)
    settings.renderer.emit(info)


@structure.command("convert")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.argument("output_file", type=click.Path())
@click.option("--format", "fmt_choice", type=click.Choice(FORMAT_CHOICES), required=True, help="目标格式")
@click.option("--name", default=None, help="litematic Metadata.Name（默认沿用源文件/输出文件名）")
@click.option("--data-version", type=int, default=None, help=f"DataVersion（默认 {DEFAULT_DATA_VERSION}）")
def structure_convert(ctx, input_file, output_file, fmt_choice, name, data_version):
    """转换结构文件格式（nbt <-> litematic）。"""
    settings: CliContext = ctx.obj

    try:
        raw = Path(input_file).read_bytes()
    except OSError as exc:
        raise UsageError(f"读取文件失败: {input_file}: {exc}") from None
    _, tree = parse_nbt(raw)
    source_format = detect_format(tree)
    if source_format == FORMAT_NBT:
        grid = parse_structure(tree)
        source_name = None
    else:
        grid = parse_litematic_tree(tree)
        meta = tree.get("Metadata")
        meta_name = meta.get("Name") if isinstance(meta, dict) else None
        source_name = meta_name if isinstance(meta_name, str) else None

    version = _data_version(data_version)
    blob = _build_blob(
        fmt_choice,
        grid,
        data_version=version,
        name=name or source_name or Path(output_file).stem,
    )

    _write_result(
        settings,
        {
            "ok": True,
            "from": source_format,
            "to": fmt_choice,
            "size": list(grid.size),
            "volume": grid.volume,
            "block_count": grid.block_count,
            "palette_size": len(grid.palette),
            "data_version": version,
        },
        blob,
        output_file,
        "structure convert",
    )
