"""level.dat / session.lock 生成与读取（1.18+ 扁平化结构）。

* ``MC_VERSIONS`` 是唯一支持的版本表（1.20.1 -> DataVersion 3465）
* ``level.dat`` = gzip NBT，根名为空串，内容位于 ``Data`` compound，
  成功创建的世界还会带 ``session.lock``（UTF-8 的 ``☃``，3 字节）
* 输出确定性：``Time``=0、``DayTime``=6000、种子缺省 0、
  调色板/Dimensions 顺序固定
"""

from __future__ import annotations

from typing import Any

from mmffc.core.errors import SchemaError, UsageError
from mmffc.formats.nbt import (
    Byte,
    Int,
    Long,
    ListOf,
    TAG_STRING,
    gzip_compress,
    parse_nbt,
    write_nbt,
)

DATA_VERSION = 3465  # Minecraft 1.20.1
LEVEL_VERSION = 19133
VERSION_NAME = "1.20.1"
SERIES = "main"

MC_VERSIONS: dict[str, int] = {VERSION_NAME: DATA_VERSION}

SPAWN_X, SPAWN_Y, SPAWN_Z = 0, 64, 0
SESSION_LOCK_TEXT = "☃"
GAME_TYPE = 1
DIFFICULTY = 2


def resolve_data_version(mc_version: str) -> int:
    """游戏版本字符串 -> DataVersion（不支持时报 UsageError，exit 2）。"""
    try:
        return MC_VERSIONS[mc_version]
    except KeyError:
        supported = ", ".join(sorted(MC_VERSIONS))
        raise UsageError(
            f"暂不支持游戏版本 {mc_version!r}（支持: {supported}）"
        ) from None


def session_lock_bytes() -> bytes:
    """session.lock 内容（UTF-8 编码的 ☃）。"""
    return SESSION_LOCK_TEXT.encode("utf-8")


def _generator(settings: str, biome_source: dict) -> dict:
    return {
        "type": "minecraft:noise",
        "settings": settings,
        "biome_source": biome_source,
    }


def _dimension(key: str, settings: str, biome_source: dict) -> dict:
    return {
        "type": key,
        "generator": _generator(settings, biome_source),
    }


def build_level_dat(
    *,
    mc_version: str = VERSION_NAME,
    level_name: str = "MMFFC World",
    seed: int = 0,
) -> bytes:
    """构造确定性 level.dat 字节。"""
    data_version = resolve_data_version(mc_version)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise UsageError(f"--seed 必须是整数: {seed!r}")

    data: dict[str, Any] = {
        "DataVersion": Int(data_version),
        "version": Int(LEVEL_VERSION),
        "LevelName": str(level_name),
        "GameType": Int(GAME_TYPE),
        "Difficulty": Int(DIFFICULTY),
        "SpawnX": Int(SPAWN_X),
        "SpawnY": Int(SPAWN_Y),
        "SpawnZ": Int(SPAWN_Z),
        "Time": Long(0),
        "DayTime": Long(6000),
        "allowCommands": Byte(1),
        "initialized": Byte(1),
        "WorldGenSettings": {
            "seed": Long(seed),
            "generate_features": Byte(0),
            "bonus_chest": Byte(0),
            "dimensions": {
                "minecraft:overworld": _dimension(
                    "minecraft:overworld",
                    "minecraft:overworld",
                    {
                        "type": "minecraft:multi_noise",
                        "preset": "minecraft:overworld",
                    },
                ),
                "minecraft:the_nether": _dimension(
                    "minecraft:the_nether",
                    "minecraft:the_nether",
                    {
                        "type": "minecraft:multi_noise",
                        "preset": "minecraft:the_nether",
                    },
                ),
                "minecraft:the_end": _dimension(
                    "minecraft:the_end",
                    "minecraft:the_end",
                    {"type": "minecraft:the_end"},
                ),
            },
        },
        "DataPacks": {
            "Enabled": ListOf(TAG_STRING, ["vanilla"]),
            "Disabled": ListOf(TAG_STRING, []),
        },
        "Version": {
            "Id": Int(data_version),
            "Name": VERSION_NAME,
            "Series": SERIES,
            "Snapshot": Byte(0),
        },
    }
    return gzip_compress(write_nbt("", {"Data": data}))


def parse_level_dat(raw: bytes) -> dict[str, Any]:
    """解析 level.dat，返回 ``Data`` compound（损坏抛 SchemaError）。"""
    name, tree = parse_nbt(raw)
    if name != "":
        raise SchemaError("level.dat 根名称应为空串")
    if not isinstance(tree, dict):
        raise SchemaError("level.dat 根节点必须是 compound")
    data = tree.get("Data")
    if not isinstance(data, dict):
        raise SchemaError("level.dat 缺少 Data compound")
    return data


def read_data_version(raw: bytes) -> int:
    """从 level.dat 读 DataVersion（缺失抛 SchemaError）。"""
    data = parse_level_dat(raw)
    version = data.get("DataVersion")
    if not isinstance(version, int) or isinstance(version, bool):
        raise SchemaError("level.dat 缺少 DataVersion")
    return version


def inspect_level_dat(raw: bytes) -> dict[str, Any]:
    """level.dat 摘要（供 world inspect / validate）。"""
    data = parse_level_dat(raw)
    world_gen = data.get("WorldGenSettings")
    seed = None
    if isinstance(world_gen, dict):
        raw_seed = world_gen.get("seed")
        if isinstance(raw_seed, int) and not isinstance(raw_seed, bool):
            seed = int(raw_seed)
    version_name = data.get("Version")
    if isinstance(version_name, dict):
        version_name = version_name.get("Name")
    return {
        "level_name": data.get("LevelName"),
        "game_version": version_name if isinstance(version_name, str) else None,
        "data_version": data.get("DataVersion"),
        "game_type": data.get("GameType"),
        "seed": seed,
        "spawn": [SPAWN_X, SPAWN_Y, SPAWN_Z],
        "time": data.get("Time"),
        "day_time": data.get("DayTime"),
    }


def initial_seed(rules: dict[str, Any]) -> int:
    """规则里的 seed（默认 0，供 level.dat 覆盖）。"""
    seed = rules.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise SchemaError(f"规则 seed 必须是整数: {seed!r}")
    return seed


__all__ = [
    "DATA_VERSION",
    "LEVEL_VERSION",
    "MC_VERSIONS",
    "SESSION_LOCK_TEXT",
    "build_level_dat",
    "initial_seed",
    "inspect_level_dat",
    "parse_level_dat",
    "read_data_version",
    "resolve_data_version",
    "session_lock_bytes",
]
