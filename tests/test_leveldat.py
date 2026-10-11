"""level.dat / session.lock：结构、确定性、版本表与错误契约。"""

from __future__ import annotations

import gzip

import pytest

from mmffc.core.errors import SchemaError, UsageError
from mmffc.formats.leveldat import (
    DATA_VERSION,
    LEVEL_VERSION,
    MC_VERSIONS,
    build_level_dat,
    inspect_level_dat,
    parse_level_dat,
    read_data_version,
    resolve_data_version,
    session_lock_bytes,
)
from mmffc.formats.nbt import parse_nbt


def _data() -> dict:
    return parse_level_dat(build_level_dat())


# ----------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------
def test_root_is_gzip_with_empty_name():
    raw = build_level_dat()
    assert raw[:2] == b"\x1f\x8b"
    name, tree = parse_nbt(raw)
    assert name == ""
    assert set(tree) == {"Data"}


def test_data_fields():
    data = _data()
    assert data["DataVersion"] == DATA_VERSION == 3465
    assert data["version"] == LEVEL_VERSION == 19133
    assert data["LevelName"] == "MMFFC World"
    assert data["SpawnX"] == 0 and data["SpawnY"] == 64 and data["SpawnZ"] == 0
    assert data["Time"] == 0 and data["DayTime"] == 6000
    assert data["Version"] == {
        "Id": DATA_VERSION,
        "Name": "1.20.1",
        "Series": "main",
        "Snapshot": 0,
    }
    assert list(data["DataPacks"]["Enabled"]) == ["vanilla"]
    assert list(data["DataPacks"]["Disabled"]) == []


def test_world_gen_settings_dimensions():
    world_gen = _data()["WorldGenSettings"]
    assert world_gen["seed"] == 0
    assert set(world_gen) == {
        "seed",
        "generate_features",
        "bonus_chest",
        "dimensions",
    }
    assert set(world_gen["dimensions"]) == {
        "minecraft:overworld",
        "minecraft:the_nether",
        "minecraft:the_end",
    }
    overworld = world_gen["dimensions"]["minecraft:overworld"]
    assert overworld["type"] == "minecraft:overworld"
    assert overworld["generator"]["settings"] == "minecraft:overworld"
    assert overworld["generator"]["biome_source"]["type"] == "minecraft:multi_noise"
    assert overworld["generator"]["biome_source"]["preset"] == "minecraft:overworld"
    end = world_gen["dimensions"]["minecraft:the_end"]
    assert end["generator"]["biome_source"]["type"] == "minecraft:the_end"


def test_custom_name_and_seed():
    data = parse_level_dat(
        build_level_dat(level_name="测试世界", seed=-1234567890123)
    )
    assert data["LevelName"] == "测试世界"
    assert data["WorldGenSettings"]["seed"] == -1234567890123


def test_build_is_deterministic():
    assert build_level_dat() == build_level_dat()
    assert build_level_dat(seed=42) == build_level_dat(seed=42)


def test_gzip_mtime_is_zero():
    raw = build_level_dat()
    assert raw[4:8] == b"\x00\x00\x00\x00"


# ----------------------------------------------------------------------
# session.lock
# ----------------------------------------------------------------------
def test_session_lock_bytes():
    blob = session_lock_bytes()
    assert blob == "☃".encode("utf-8")
    assert blob == b"\xe2\x98\x83"
    assert len(blob) == 3


# ----------------------------------------------------------------------
# 版本表
# ----------------------------------------------------------------------
def test_resolve_data_version():
    assert resolve_data_version("1.20.1") == DATA_VERSION


def test_unsupported_version_rejected():
    with pytest.raises(UsageError, match="暂不支持"):
        resolve_data_version("1.21.4")


def test_mc_versions_only_lists_supported():
    assert MC_VERSIONS == {"1.20.1": DATA_VERSION}


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------
def test_read_data_version():
    assert read_data_version(build_level_dat()) == DATA_VERSION


def test_parse_rejects_non_gzip():
    with pytest.raises(SchemaError):
        parse_level_dat(b"not nbt at all")


def test_parse_rejects_missing_data():
    from mmffc.formats.nbt import gzip_compress, write_nbt

    raw = gzip_compress(write_nbt("", {"Other": 1}))
    with pytest.raises(SchemaError, match="缺少 Data"):
        parse_level_dat(raw)


def test_parse_rejects_wrong_root_name():
    from mmffc.formats.nbt import gzip_compress, write_nbt

    raw = gzip_compress(write_nbt("level", {"Data": {}}))
    with pytest.raises(SchemaError, match="根名称"):
        parse_level_dat(raw)


def test_read_data_version_missing():
    from mmffc.formats.nbt import gzip_compress, write_nbt

    raw = gzip_compress(write_nbt("", {"Data": {"LevelName": "x"}}))
    with pytest.raises(SchemaError, match="DataVersion"):
        read_data_version(raw)


# ----------------------------------------------------------------------
# inspect
# ----------------------------------------------------------------------
def test_inspect_level_dat():
    info = inspect_level_dat(build_level_dat(level_name="Test", seed=7))
    assert info["level_name"] == "Test"
    assert info["game_version"] == "1.20.1"
    assert info["data_version"] == DATA_VERSION
    assert info["seed"] == 7
    assert info["spawn"] == [0, 64, 0]
    assert info["day_time"] == 6000
