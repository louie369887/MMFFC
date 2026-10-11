"""mmffc world CLI：五命令的退出码、落盘契约与管线组合。"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from mmffc.cli import main

FIXTURE = "".join(
    json.dumps({"x": x, "y": 64, "z": z, "block": "minecraft:stone"}) + "\n"
    for x in range(3)
    for z in range(3)
)
FIXTURE += json.dumps({"x": 1, "y": 65, "z": 1, "block": "minecraft:gold_block"}) + "\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def world(home):
    """已初始化的世界目录。"""
    target = home / "saves" / "test"
    code = main(["world", "init", str(target), "--mc-version", "1.20.1",
                 "--name", "Test"])
    assert code == 0
    return target


def _ndjson(home: Path, text: str = FIXTURE, name: str = "chunks.ndjson") -> Path:
    path = home / name
    path.write_text(text, encoding="utf-8")
    return path


def _compile(world: Path, src: Path, *extra):
    return main(["world", "compile", "--input", str(src),
                 "--output", str(world), *extra])


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _decode_region(path: Path) -> dict[tuple[int, int, int], str]:
    """解码区域文件 -> {(x, y, z): block_name}（测试用端到端校验）。"""
    from mmffc.formats.anvil import parse_region
    from mmffc.formats.chunk import _unpack
    from mmffc.formats.nbt import parse_nbt

    blocks: dict[tuple[int, int, int], str] = {}
    for blob in parse_region(path.read_bytes()).values():
        _, tree = parse_nbt(blob)
        cx, cz = tree["xPos"], tree["zPos"]
        for section in tree["sections"]:
            palette = section["block_states"]["palette"]
            data = section["block_states"].get("data")
            if data is None:
                continue
            bits = max(4, (len(palette) - 1).bit_length())
            per_long = 64 // bits
            values: list[int] = []
            for long_value in data:
                for offset in range(per_long):
                    values.append((long_value >> (offset * bits)) & ((1 << bits) - 1))
            for index, state in enumerate(values[:4096]):
                ly, rest = divmod(index, 256)
                lz, lx = divmod(rest, 16)
                name = palette[state]["Name"]
                if name != "minecraft:air":
                    blocks[(cx * 16 + lx, section["Y"] * 16 + ly, cz * 16 + lz)] = name
    return blocks


# ----------------------------------------------------------------------
# world init
# ----------------------------------------------------------------------
def test_init_creates_world_files(home, capsys):
    target = home / "fresh"
    code = main(["world", "init", str(target), "--mc-version", "1.20.1",
                 "--name", "Test", "--json"])
    assert code == 0
    data = _payload(capsys)
    assert data["mc_version"] == "1.20.1"
    assert data["data_version"] == 3465
    assert data["dry_run"] is False
    assert (target / "level.dat").is_file()
    assert (target / "session.lock").read_bytes() == b"\xe2\x98\x83"
    assert (target / "region").is_dir()


def test_init_dry_run_writes_nothing(home, capsys):
    target = home / "fresh"
    code = main(["world", "init", str(target), "--mc-version", "1.20.1",
                 "--dry-run", "--json"])
    assert code == 0
    data = _payload(capsys)
    assert data["dry_run"] is True
    assert data["backup"] is None
    assert not target.exists()


def test_init_rejects_unsupported_version(home, capsys):
    code = main(["world", "init", str(home / "w"), "--mc-version", "1.21.4"])
    assert code == 2
    assert "暂不支持" in capsys.readouterr().err


def test_init_conflict_needs_yes(world, capsys):
    code = main(["world", "init", str(world), "--mc-version", "1.20.1"])
    assert code == 5
    assert "--yes" in capsys.readouterr().err


def test_init_yes_overwrites_with_backup(home, capsys):
    target = home / "saves" / "test"
    first = main(["world", "init", str(target), "--mc-version", "1.20.1",
                  "--name", "One"])
    assert first == 0
    capsys.readouterr()
    code = main(["world", "init", str(target), "--mc-version", "1.20.1",
                 "--name", "Two", "--yes", "--json"])
    assert code == 0
    data = _payload(capsys)
    assert data["name"] == "Two"
    assert data["backup"]
    backup = Path(data["backup"])
    assert backup.is_file()
    assert backup.name == "level.dat"


def test_init_table_output_no_ansi(home, capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    code = main(["world", "init", str(home / "w"), "--mc-version", "1.20.1"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "1.20.1" in out


# ----------------------------------------------------------------------
# world compile
# ----------------------------------------------------------------------
def test_compile_writes_region_and_validates(home, world, capsys):
    code = _compile(world, _ndjson(home), "--yes", "--json")
    assert code == 0
    data = _payload(capsys)
    assert data["chunk_count"] == 1
    assert data["regions"] == ["r.0.0.mca"]
    assert data["block_count"] == 10
    assert data["dry_run"] is False

    region = world / "region" / "r.0.0.mca"
    assert region.is_file()
    assert region.stat().st_size % 4096 == 0

    assert main(["world", "validate", str(world), "--json"]) == 0
    assert _payload(capsys)["valid"] is True


def test_compile_requires_yes_in_non_tty(home, world, capsys):
    assert _compile(world, _ndjson(home)) == 2
    assert "--yes" in capsys.readouterr().err
    assert not (world / "region" / "r.0.0.mca").exists()


def test_compile_requires_level_dat(home, capsys):
    target = home / "empty"
    target.mkdir()
    code = main(["world", "compile", "--input", str(_ndjson(home)),
                 "--output", str(target), "--yes"])
    assert code == 2
    assert "world init" in capsys.readouterr().err


def test_compile_stdin_pipe(home, world, monkeypatch, capsys):
    def fake(name):
        if name == "stdin":
            import io

            return io.StringIO(FIXTURE)
        return original(name)

    original = click.get_text_stream
    monkeypatch.setattr(click, "get_text_stream", fake)
    code = main(["world", "compile", "--input", "-",
                 "--output", str(world), "--yes", "--json"])
    assert code == 0
    assert _payload(capsys)["chunk_count"] == 1


def test_compile_dry_run_does_not_write(home, world, capsys):
    code = _compile(world, _ndjson(home), "--dry-run", "--json")
    assert code == 0
    data = _payload(capsys)
    assert data["dry_run"] is True
    assert data["written"] == []
    assert not (world / "region" / "r.0.0.mca").exists()


def test_compile_bad_ndjson_exits_3(home, world, capsys):
    bad = home / "bad.ndjson"
    bad.write_text("{broken\n", encoding="utf-8")
    assert _compile(world, bad, "--yes") == 3
    assert "第 1 行" in capsys.readouterr().err


def test_compile_y_out_of_range_exits_3(home, world, capsys):
    text = json.dumps({"x": 0, "y": 320, "z": 0, "block": "stone"}) + "\n"
    assert _compile(world, _ndjson(home, text), "--yes") == 3
    assert "高度范围" in capsys.readouterr().err


def test_compile_region_mismatch_exits_3(home, world, capsys):
    text = json.dumps({"x": 512, "y": 64, "z": 0, "block": "stone"}) + "\n"
    assert _compile(world, _ndjson(home, text), "--region", "0,0", "--yes") == 3
    assert "--region" in capsys.readouterr().err


def test_compile_region_match_accepts_neighbour(home, world, capsys):
    text = json.dumps({"x": 512, "y": 64, "z": 0, "block": "stone"}) + "\n"
    assert _compile(world, _ndjson(home, text), "--region", "1,0", "--yes",
                    "--json") == 0
    assert _payload(capsys)["regions"] == ["r.1.0.mca"]


def test_compile_merges_existing_chunks(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    capsys.readouterr()

    other = home / "second.ndjson"
    other.write_text(
        json.dumps({"x": 16, "y": 64, "z": 16, "block": "minecraft:dirt"}) + "\n",
        encoding="utf-8",
    )
    assert _compile(world, other, "--yes", "--json") == 0
    data = _payload(capsys)
    assert data["chunk_count"] == 1

    assert main(["world", "validate", str(world), "--json"]) == 0
    info = _payload(capsys)
    assert info["chunk_count"] == 2

    assert main(["world", "inspect", str(world), "--json"]) == 0
    levels = _payload(capsys)["regions"]
    assert len(levels) == 1
    assert levels[0]["chunks"] == 2


def test_compile_second_write_creates_backup(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    capsys.readouterr()
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    data = _payload(capsys)
    assert len(data["written"]) == 2  # 区域文件 + 备份
    assert all(Path(path).is_file() for path in data["written"])


def test_compile_heightmap_none(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--heightmap", "none",
                    "--json") == 0
    _payload(capsys)

    assert main(["world", "validate", str(world), "--json"]) == 0
    data = _payload(capsys)
    assert data["valid"] is True
    assert any("Heightmaps" in item for item in data["warnings"])


def test_compile_bom_input_tolerated(home, world, capsys):
    src = home / "bom.ndjson"
    src.write_bytes(b"\xef\xbb\xbf" + FIXTURE.encode("utf-8"))
    assert _compile(world, src, "--yes", "--json") == 0
    assert _payload(capsys)["block_count"] == 10


def test_compile_multi_chunk(home, world, capsys):
    lines = []
    for cx in range(2):
        for x in range(2):
            lines.append(json.dumps({"x": cx * 16 + x, "y": 64, "z": 0,
                                     "block": "stone"}))
    src = home / "multi.ndjson"
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _compile(world, src, "--yes", "--json") == 0
    data = _payload(capsys)
    assert data["chunk_count"] == 2
    assert data["regions"] == ["r.0.0.mca"]


def test_compile_max_voxels_budget(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--max-voxels", "5",
                    "--json") == 3
    assert "--max-voxels" in capsys.readouterr().err
    assert not (world / "region" / "r.0.0.mca").exists()


def test_compile_max_voxels_reported(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--max-voxels", "50",
                    "--json") == 0
    assert _payload(capsys)["max_voxels"] == 50


def test_build_max_voxels_budget(home, world, capsys):
    rules = _rules(home, {
        "area": {"x": 0, "z": 0, "w": 8, "d": 8},
        "layers": [{"from": 0, "to": 0, "block": "stone"}],
    })
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--yes", "--max-voxels", "10"]) == 3
    assert "预算" in capsys.readouterr().err


# ----------------------------------------------------------------------
# world build
# ----------------------------------------------------------------------
def _rules(home: Path, doc: dict) -> Path:
    path = home / "rules.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_build_from_rules(home, world, capsys):
    rules = _rules(home, {
        "name": "test",
        "area": {"x": 0, "z": 0, "w": 4, "d": 4},
        "layers": [
            {"from": 63, "to": 63, "block": "stone"},
            {"from": 64, "to": 64, "block": "grass_block"},
        ],
    })
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--yes", "--json"]) == 0
    data = _payload(capsys)
    assert data["chunk_count"] == 1
    assert data["block_count"] == 32
    assert (world / "region" / "r.0.0.mca").is_file()
    assert main(["world", "validate", str(world), "--json"]) == 0


def test_build_layers_ops_blocks_order(home, world, capsys):
    rules = _rules(home, {
        "area": {"x": 0, "z": 0, "w": 2, "d": 2},
        "layers": [{"from": 64, "to": 64, "block": "dirt"}],
        "ops": [{"shape": "box", "x": 0, "y": 64, "z": 1,
                 "w": 1, "h": 1, "d": 1, "block": "stone"}],
        "blocks": [{"x": 0, "y": 64, "z": 0, "block": "gold_block"}],
    })
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--yes", "--json"]) == 0
    data = _payload(capsys)
    assert data["block_count"] == 4  # 2x2 层：2 dirt + 1 stone + 1 gold

    assert main(["world", "validate", str(world), "--json"]) == 0
    blocks = _decode_region(world / "region" / "r.0.0.mca")
    assert blocks[(0, 64, 0)] == "minecraft:gold_block"  # blocks 最后覆盖
    assert blocks[(0, 64, 1)] == "minecraft:stone"  # ops 覆盖层
    assert blocks[(1, 64, 0)] == "minecraft:dirt"  # 层内未被触及


def test_build_requires_yes(home, world, capsys):
    rules = _rules(home, {
        "area": {"x": 0, "z": 0, "w": 1, "d": 1},
        "layers": [{"from": 0, "to": 0, "block": "stone"}],
    })
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world)]) == 2
    assert "--yes" in capsys.readouterr().err


def test_build_dry_run(home, world, capsys):
    rules = _rules(home, {
        "area": {"x": 0, "z": 0, "w": 1, "d": 1},
        "layers": [{"from": 0, "to": 0, "block": "stone"}],
    })
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--dry-run", "--json"]) == 0
    data = _payload(capsys)
    assert data["dry_run"] is True
    assert not (world / "region" / "r.0.0.mca").exists()


@pytest.mark.parametrize(
    "doc,needle",
    [
        ({"layers": [{"from": 0, "to": 0, "block": "stone"}]}, "area"),
        ({"unknown": 1}, "未知字段"),
        ({"area": {"x": 0, "z": 0, "w": 1, "d": 1}}, "至少一项"),
        ({"area": {"x": 0, "z": 0, "w": 1, "d": 1},
          "layers": [{"from": 5, "to": 1, "block": "stone"}]}, "from 必须 <="),
        ({"area": {"x": 0, "z": 0, "w": 1, "d": 1},
          "layers": [{"to": 1, "block": "stone"}]}, "缺少字段"),
        ({"area": {"x": 0, "z": 0, "w": 1, "d": 1},
          "layers": [{"from": 0, "to": 0, "block": "not a block!"}]}, "方块 ID"),
        ({"blocks": [{"x": 0, "y": 0, "block": "stone"}]}, "缺少字段 z"),
        ({"blocks": [{"x": 0, "y": 0, "z": 0, "block": "air"}]}, "空气"),
    ],
)
def test_build_rejects_bad_rules(home, world, capsys, doc, needle):
    rules = _rules(home, doc)
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--yes"]) == 3
    assert needle in capsys.readouterr().err


def test_build_rejects_malformed_json(home, world, capsys):
    rules = home / "rules.json"
    rules.write_text("{not json", encoding="utf-8")
    assert main(["world", "build", "--rules", str(rules),
                 "--output", str(world), "--yes"]) == 3
    assert "JSON" in capsys.readouterr().err


# ----------------------------------------------------------------------
# world validate
# ----------------------------------------------------------------------
def test_validate_clean_world(world, capsys):
    assert main(["world", "validate", str(world), "--json"]) == 0
    data = _payload(capsys)
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["chunk_count"] == 0
    assert any("没有区域文件" in item for item in data["warnings"])


def test_validate_missing_level_dat(home, capsys):
    target = home / "broken"
    target.mkdir()
    assert main(["world", "validate", str(target), "--json"]) == 8
    data = _payload(capsys)
    assert data["valid"] is False
    assert data["errors"] == ["缺少 level.dat"]


def test_validate_missing_session_lock_warns(home, capsys):
    target = home / "saves" / "test"
    assert main(["world", "init", str(target), "--mc-version", "1.20.1"]) == 0
    capsys.readouterr()
    (target / "session.lock").unlink()
    assert main(["world", "validate", str(target), "--json"]) == 0
    data = _payload(capsys)
    assert data["valid"] is True
    assert any("session.lock" in item for item in data["warnings"])


def test_validate_corrupt_region_exits_8(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    capsys.readouterr()
    (world / "region" / "r.0.0.mca").write_bytes(bytes(4096))
    assert main(["world", "validate", str(world), "--json"]) == 8
    data = _payload(capsys)
    assert data["valid"] is False
    assert any("过短" in item for item in data["errors"])


def test_validate_corrupt_chunk_exits_8(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    capsys.readouterr()
    region = world / "region" / "r.0.0.mca"
    raw = bytearray(region.read_bytes())
    raw[8192 + 5] ^= 0xFF  # 破坏压缩数据首字节（非补零）
    region.write_bytes(bytes(raw))
    assert main(["world", "validate", str(world), "--json"]) == 8
    data = _payload(capsys)
    assert data["valid"] is False
    assert data["errors"]


def test_validate_corrupt_level_dat_exits_8(home, world, capsys):
    (world / "level.dat").write_bytes(b"\x1f\x8b\x08\x00garbage")
    assert main(["world", "validate", str(world), "--json"]) == 8
    data = _payload(capsys)
    assert any("level.dat" in item for item in data["errors"])


def test_validate_ignores_non_region_files(home, world, capsys):
    (world / "region" / "notes.txt").write_text("hi", encoding="utf-8")
    assert main(["world", "validate", str(world), "--json"]) == 0
    assert _payload(capsys)["valid"] is True


# ----------------------------------------------------------------------
# world inspect
# ----------------------------------------------------------------------
def test_inspect_world_summary(home, world, capsys):
    assert _compile(world, _ndjson(home), "--yes", "--json") == 0
    capsys.readouterr()
    assert main(["world", "inspect", str(world), "--json"]) == 0
    data = _payload(capsys)
    assert data["ok"] is True
    assert data["level"]["level_name"] == "Test"
    assert data["level"]["game_version"] == "1.20.1"
    assert data["level"]["data_version"] == 3465
    assert data["level"]["seed"] == 0
    assert data["session_lock"] is True
    assert data["chunk_count"] == 1
    assert data["regions"] == [
        {"file": "r.0.0.mca", "bytes": data["regions"][0]["bytes"], "chunks": 1}
    ]


def test_inspect_empty_world(world, capsys):
    assert main(["world", "inspect", str(world), "--json"]) == 0
    data = _payload(capsys)
    assert data["chunk_count"] == 0
    assert data["regions"] == []


def test_inspect_missing_level_dat_exits_2(home, capsys):
    target = home / "nope"
    target.mkdir()
    assert main(["world", "inspect", str(target), "--json"]) == 2


def test_inspect_corrupt_level_dat_exits_8(home, world, capsys):
    (world / "level.dat").write_bytes(b"not-nbt")
    assert main(["world", "inspect", str(world), "--json"]) == 8
    assert "损坏" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 全局选项 / help
# ----------------------------------------------------------------------
def test_global_json_before_subcommand(home, world, capsys):
    src = _ndjson(home)
    code = main(["--format", "json", "world", "compile", "--input", str(src),
                 "--output", str(world), "--yes"])
    assert code == 0
    assert _payload(capsys)["chunk_count"] == 1


def test_world_help_lists_commands(capsys):
    code = main(["world", "--help"])
    assert code == 0
    out = capsys.readouterr().out
    for word in ("init", "compile", "build", "validate", "inspect"):
        assert word in out
