"""mmffc world — MCA 编译与世界生成（Phase 4，CLI.md 3.4）。

管线（体素 NDJSON / 规则文件 -> Anvil 世界）::

    world init <DIR> --mc-version 1.20.1 --name "Test"          # level.dat + session.lock
    world compile --input - --output <DIR> [--region 0,0]       # NDJSON -> r.0.0.mca
    world build --rules rules.json --output <DIR>               # 规则 -> mca
    world validate <DIR> [--json]                               # 完整性校验

约束：``compile`` / ``build`` 要求目标目录已有 ``level.dat``
（缺失报 UsageError，提示先 ``world init``）。区块的 DataVersion 从
level.dat 读取，保证与目标世界一致。

退出码：输入数据错误 3；世界盘上文件损坏 8；缺失前置文件 2；
覆盖已有 level.dat 未加 --yes 报 5。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import ConflictError, IntegrityError, SchemaError, UsageError
from mmffc.core.tty import require_yes
from mmffc.core.worldgen import generate_from_rules, validate_rules
from mmffc.core.voxels import parse_ndjson
from mmffc.formats.anvil import (
    build_region,
    local_index,
    parse_region,
    region_filename,
)
from mmffc.formats.chunk import (
    MAX_Y,
    MIN_Y,
    build_chunk,
    validate_chunk,
)
from mmffc.formats.leveldat import (
    MC_VERSIONS,
    build_level_dat,
    inspect_level_dat,
    parse_level_dat,
    read_data_version,
    session_lock_bytes,
)
from mmffc.formats.nbt import parse_nbt
from mmffc.io.atomic import atomic_write_bytes

LEVEL_DAT = "level.dat"
SESSION_LOCK = "session.lock"
REGION_DIR = "region"
MAX_WORLD_COORD = 30_000_000
HEIGHTMAP_CHOICES = ["auto", "none"]


@mmffc_group()
def world(ctx, **_kwargs) -> None:
    """MCA 编译与世界生成（init / compile / build / validate / inspect）。"""


def _read_input(input_path: str | None) -> str:
    if input_path is None or input_path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(input_path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UsageError(f"读取输入失败: {input_path}: {exc}") from None


def _parse_region_arg(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        raise UsageError(f"--region 必须是 X,Z 格式: {value!r}")
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        raise UsageError(f"--region 必须是整数: {value!r}") from None


def _require_data_version(world_dir: Path) -> int:
    """读取目标世界 level.dat 的 DataVersion（缺失报 2，损坏报 8）。"""
    level_path = world_dir / LEVEL_DAT
    if not level_path.is_file():
        raise UsageError(
            f"目标世界缺少 {LEVEL_DAT}: {world_dir}（先执行 mmffc world init）"
        )
    try:
        return read_data_version(level_path.read_bytes())
    except OSError as exc:
        raise IntegrityError(f"读取 {LEVEL_DAT} 失败: {exc}") from exc
    except SchemaError as exc:
        raise IntegrityError(f"{LEVEL_DAT} 损坏: {exc.message}") from exc


def _check_bounds(voxels) -> None:
    for voxel in voxels:
        if not MIN_Y <= voxel.y <= MAX_Y:
            raise SchemaError(
                f"Y={voxel.y} 超出区块高度范围 [{MIN_Y},{MAX_Y}]: "
                f"{(voxel.x, voxel.y, voxel.z)}"
            )
        if abs(voxel.x) > MAX_WORLD_COORD or abs(voxel.z) > MAX_WORLD_COORD:
            raise SchemaError(
                f"X/Z 超出世界边界 ±{MAX_WORLD_COORD}: "
                f"{(voxel.x, voxel.y, voxel.z)}"
            )


def _read_region(path: Path) -> dict[int, bytes]:
    """读取已有区域文件（损坏抛 IntegrityError，exit 8）。"""
    try:
        return parse_region(path.read_bytes())
    except OSError as exc:
        raise IntegrityError(f"读取区域文件失败: {path}: {exc}") from exc
    except SchemaError as exc:
        raise IntegrityError(f"区域文件损坏: {path}: {exc.message}") from exc


def _compile_chunks(
    settings: CliContext,
    world_dir: Path,
    voxels,
    *,
    region: tuple[int, int] | None,
    heightmap: str,
    source: str,
    max_voxels: int | None = None,
) -> None:
    """体素 -> 分区块 -> 按区域写 r.X.Z.mca（与已有区块合并）。"""
    data_version = _require_data_version(world_dir)
    _check_bounds(voxels)

    if max_voxels is not None and len(voxels) > max_voxels:
        raise SchemaError(
            f"体素数 {len(voxels)} 超过 --max-voxels 预算 {max_voxels}"
            "（请拆分成多批调用）"
        )

    grouped: dict[tuple[int, int], list] = {}
    for voxel in voxels:
        cx, cz = voxel.x >> 4, voxel.z >> 4
        if region is not None and (cx >> 5, cz >> 5) != region:
            raise SchemaError(
                f"体素 {(voxel.x, voxel.y, voxel.z)} 属于区域 "
                f"({cx >> 5},{cz >> 5})，与 --region "
                f"{region[0]},{region[1]} 不符"
            )
        grouped.setdefault((cx, cz), []).append(voxel)

    regions: dict[tuple[int, int], dict[int, bytes]] = {}
    for (cx, cz), cells in sorted(grouped.items()):
        index = local_index(cx, cz)
        regions.setdefault((cx >> 5, cz >> 5), {})[index] = build_chunk(
            cx,
            cz,
            cells,
            data_version=data_version,
            heightmap=heightmap == "auto",
        )

    payload = {
        "ok": True,
        "source": source,
        "chunk_count": len(grouped),
        "block_count": len(voxels),
        "region_count": len(regions),
        "regions": [region_filename(rx, rz) for rx, rz in sorted(regions)],
        "heightmap": heightmap,
        "data_version": data_version,
        "max_voxels": max_voxels,
    }

    if settings.dry_run:
        settings.renderer.emit({**payload, "dry_run": True, "written": []})
        return

    require_yes(settings.yes, f"world compile --output {world_dir}")

    written: list[str] = []
    region_dir = world_dir / REGION_DIR
    for (rx, rz), new_chunks in sorted(regions.items()):
        path = region_dir / region_filename(rx, rz)
        if path.exists():
            merged = _read_region(path)
            merged.update(new_chunks)
        else:
            merged = dict(new_chunks)
        backup = atomic_write_bytes(
            path,
            build_region(merged),
            backup_root=settings.config.backups_dir,
        )
        written.append(str(path))
        if backup:
            written.append(str(backup))

    settings.renderer.emit({
        **payload,
        "dry_run": False,
        "written": written,
    })


@world.command("init")
@click.argument("dir", type=click.Path(file_okay=False))
@click.option(
    "--mc-version",
    "mc_version",
    required=True,
    help=f"游戏版本（{', '.join(sorted(MC_VERSIONS))}）",
)
@click.option("--name", default="MMFFC World", help="level.dat LevelName")
@click.option("--seed", type=int, default=0, help="世界种子（默认 0）")
def world_init(ctx, dir, mc_version, name, seed):
    """初始化世界目录（level.dat + session.lock + region/）。"""
    settings: CliContext = ctx.obj
    world_dir = Path(dir).expanduser()
    level_path = world_dir / LEVEL_DAT
    lock_path = world_dir / SESSION_LOCK

    if level_path.exists() and not settings.yes:
        raise ConflictError(
            f"{LEVEL_DAT} 已存在: {level_path}（覆盖需 --yes）",
            path=str(level_path),
        )

    data_version = MC_VERSIONS.get(mc_version)
    if data_version is None:
        supported = ", ".join(sorted(MC_VERSIONS))
        raise UsageError(
            f"暂不支持游戏版本 {mc_version!r}（支持: {supported}）"
        )

    blob = build_level_dat(mc_version=mc_version, level_name=name, seed=seed)

    payload = {
        "ok": True,
        "path": str(world_dir),
        "mc_version": mc_version,
        "data_version": data_version,
        "name": name,
        "seed": seed,
    }
    if settings.dry_run:
        settings.renderer.emit({**payload, "dry_run": True, "backup": None})
        return

    backup = atomic_write_bytes(
        level_path, blob, backup_root=settings.config.backups_dir
    )
    if not lock_path.exists():
        atomic_write_bytes(lock_path, session_lock_bytes())
    (world_dir / REGION_DIR).mkdir(parents=True, exist_ok=True)

    settings.renderer.emit({
        **payload,
        "dry_run": False,
        "backup": str(backup) if backup else None,
    })


@world.command("compile")
@click.option(
    "--input",
    "input",
    type=click.Path(),
    default=None,
    help="体素 NDJSON 文件，- 或省略表示 stdin",
)
@click.option(
    "--output",
    "output",
    type=click.Path(file_okay=False),
    required=True,
    help="目标世界目录（须已 world init）",
)
@click.option("--region", "region", default=None, help="限定区域坐标 X,Z")
@click.option(
    "--heightmap",
    type=click.Choice(HEIGHTMAP_CHOICES),
    default="auto",
    help="Heightmaps: auto=计算，none=省略",
)
@click.option(
    "--max-voxels",
    "max_voxels",
    type=int,
    default=None,
    help="单批体素预算上限（超出报错退出码 3，便于分批生成大世界）",
)
def world_compile(ctx, input, output, region, heightmap, max_voxels):
    """编译体素 NDJSON 为 Anvil 区域文件。"""
    settings: CliContext = ctx.obj
    voxels = parse_ndjson(_read_input(input))
    _compile_chunks(
        settings,
        Path(output).expanduser(),
        voxels,
        region=_parse_region_arg(region),
        heightmap=heightmap,
        source="compile",
        max_voxels=max_voxels,
    )


@world.command("build")
@click.option(
    "--rules",
    "rules",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="规则 JSON 文件",
)
@click.option(
    "--output",
    "output",
    type=click.Path(file_okay=False),
    required=True,
    help="目标世界目录（须已 world init）",
)
@click.option(
    "--heightmap",
    type=click.Choice(HEIGHTMAP_CHOICES),
    default="auto",
    help="Heightmaps: auto=计算，none=省略",
)
@click.option(
    "--max-voxels",
    "max_voxels",
    type=int,
    default=None,
    help="单批体素预算上限（超出报错退出码 3，便于分批生成大世界）",
)
def world_build(ctx, rules, output, heightmap, max_voxels):
    """按规则文件生成世界区块（rules -> mca）。"""
    settings: CliContext = ctx.obj
    world_dir = Path(output).expanduser()
    try:
        document = json.loads(Path(rules).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UsageError(f"读取规则文件失败: {rules}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise SchemaError(f"规则文件不是合法 JSON: {rules}: {exc.msg}") from None

    voxels = generate_from_rules(validate_rules(document))
    _compile_chunks(
        settings,
        world_dir,
        voxels,
        region=None,
        heightmap=heightmap,
        source=str(rules),
        max_voxels=max_voxels,
    )


def _region_files(world_dir: Path) -> list[Path]:
    region_dir = world_dir / REGION_DIR
    if not region_dir.is_dir():
        return []
    return sorted(
        path
        for path in region_dir.glob("*.mca")
        if path.name.startswith("r.")
    )


@world.command("import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False))
@click.option("--saves-dir", "saves_dir", default="saves",
              help="存档根目录（默认 ./saves）")
@click.option("--name", "name", default=None, help="存档名（默认取文件名）")
def world_import(ctx, archive, saves_dir, name):
    """从 .zip / .mcworld 导入世界存档（防 zip-slip，导入后校验）。"""
    settings: CliContext = ctx.obj
    from mmffc.packs.archive import import_world

    dest = Path(saves_dir).expanduser()
    if settings.dry_run:
        from mmffc.packs.archive import plan_archive

        plan = plan_archive(Path(archive).expanduser())
        settings.renderer.emit({
            "ok": True,
            "dry_run": True,
            "plan": plan.to_dict(),
        })
        return

    require_yes(settings.yes, f"world import {archive}")
    result = import_world(Path(archive).expanduser(), dest, name=name)
    payload = {
        "ok": True,
        "dry_run": False,
        "archive": str(Path(archive).expanduser()),
        "name": result["name"],
        "world_dir": str(result["world_dir"]),
        "saves_dir": str(dest),
        "plan": result["plan"],
    }
    settings.renderer.emit(payload)


@world.command("validate")
@click.argument("dir", type=click.Path(file_okay=False))
def world_validate(ctx, dir):
    """校验世界存档完整性（--json 输出 valid / errors / warnings）。"""
    settings: CliContext = ctx.obj
    world_dir = Path(dir).expanduser()
    errors: list[str] = []
    warnings: list[str] = []

    level_path = world_dir / LEVEL_DAT
    if not level_path.is_file():
        errors.append(f"缺少 {LEVEL_DAT}")
    else:
        try:
            parse_level_dat(level_path.read_bytes())
        except SchemaError as exc:
            errors.append(f"{LEVEL_DAT}: {exc.message}")

    if not (world_dir / SESSION_LOCK).exists():
        warnings.append(f"缺少 {SESSION_LOCK}")

    data_version: int | None = None
    if level_path.is_file() and not any(
        item.startswith(f"{LEVEL_DAT}:") for item in errors
    ):
        try:
            data_version = read_data_version(level_path.read_bytes())
        except SchemaError:
            pass

    region_files = _region_files(world_dir)
    if not region_files:
        warnings.append(f"{REGION_DIR}/ 下没有区域文件")

    total_chunks = 0
    missing_heightmaps = 0
    for path in region_files:
        try:
            chunks = _read_region(path)
        except IntegrityError as exc:
            errors.append(str(exc.message))
            continue
        except OSError as exc:
            errors.append(f"{path.name}: 读取失败: {exc}")
            continue

        parts = path.stem.split(".")
        if len(parts) != 3 or parts[0] != "r":
            errors.append(f"{path.name}: 文件名不符合 r.X.Z.mca")
            continue
        try:
            rx, rz = int(parts[1]), int(parts[2])
        except ValueError:
            errors.append(f"{path.name}: 区域坐标不是整数")
            continue

        for index, blob in sorted(chunks.items()):
            cx, cz = rx * 32 + (index % 32), rz * 32 + (index // 32)
            total_chunks += 1
            try:
                _, tree = parse_nbt(blob)
            except SchemaError as exc:
                errors.append(f"{path.name} [{cx},{cz}]: NBT 解析失败: {exc.message}")
                continue
            if not isinstance(tree, dict) or "Heightmaps" not in tree:
                missing_heightmaps += 1
            for problem in validate_chunk(
                tree, expect_x=cx, expect_z=cz, data_version=data_version
            ):
                errors.append(f"{path.name} [{cx},{cz}]: {problem}")

    if missing_heightmaps:
        warnings.append(
            f"{missing_heightmaps} 个区块缺少 Heightmaps（--heightmap none，"
            "游戏加载时会重算）"
        )

    settings.renderer.emit({
        "valid": not errors,
        "chunk_count": total_chunks,
        "region_count": len(region_files),
        "errors": errors,
        "warnings": warnings,
    })
    if errors:
        raise IntegrityError(f"世界校验失败: {len(errors)} 个错误")


@world.command("inspect")
@click.argument("dir", type=click.Path(file_okay=False))
def world_inspect(ctx, dir):
    """检查世界存档摘要（level.dat + 区域列表）。"""
    settings: CliContext = ctx.obj
    world_dir = Path(dir).expanduser()

    level_path = world_dir / LEVEL_DAT
    if not level_path.is_file():
        raise UsageError(
            f"目标世界缺少 {LEVEL_DAT}: {world_dir}（先执行 mmffc world init）"
        )
    try:
        level = inspect_level_dat(level_path.read_bytes())
    except OSError as exc:
        raise IntegrityError(f"读取 {LEVEL_DAT} 失败: {exc}") from exc
    except SchemaError as exc:
        raise IntegrityError(f"{LEVEL_DAT} 损坏: {exc.message}") from exc

    regions: list[dict[str, Any]] = []
    total_chunks = 0
    for path in _region_files(world_dir):
        try:
            chunks = _read_region(path)
        except IntegrityError:
            raise
        except OSError as exc:
            raise IntegrityError(f"读取区域文件失败: {path}: {exc}") from exc
        count = len(chunks)
        total_chunks += count
        regions.append({
            "file": path.name,
            "bytes": path.stat().st_size,
            "chunks": count,
        })

    settings.renderer.emit({
        "ok": True,
        "path": str(world_dir),
        "session_lock": (world_dir / SESSION_LOCK).exists(),
        "level": level,
        "region_count": len(regions),
        "chunk_count": total_chunks,
        "regions": regions,
    })
