"""mmffc structure CLI：编译/检查/转换的退出码与落盘契约。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import click
import pytest

from mmffc.cli import main
from mmffc.core.voxels import build_grid, parse_ndjson
from mmffc.formats.litematic import parse_litematic_tree
from mmffc.formats.nbt import parse_nbt
from mmffc.formats.structure import build_structure, parse_structure

FIXTURE = (
    '{"x":0,"y":0,"z":0,"block":"stone"}\n'
    '{"x":1,"y":0,"z":0,"block":"oak_planks","properties":{"facing":"north"}}\n'
    '{"x":0,"y":1,"z":0,"block":"glass"}\n'
)

# 3x1x2 地板（非方形，回归 litematic 索引解码）
FLOOR = "".join(
    f'{{"x":{x},"y":0,"z":{z},"block":"stone"}}\n'
    for x in range(3)
    for z in range(2)
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    return tmp_path


def _src(home, text=FIXTURE, *, bom=False, name="v.ndjson") -> Path:
    path = home / name
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    return path


def _compile(home, capsys, fmt, out_name, src, *extra):
    code = main(
        [
            "structure",
            "compile",
            "--format",
            fmt,
            "-o",
            str(home / out_name),
            "--input",
            str(src),
            "--yes",
            "--json",
            *extra,
        ]
    )
    return code, json.loads(capsys.readouterr().out)


# ----------------------------------------------------------------------
# compile
# ----------------------------------------------------------------------
def test_compile_dry_run_does_not_write(home, capsys):
    src = _src(home)
    out = home / "t.nbt"
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(out), "--input", str(src),
            "--dry-run", "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["backup"] is None
    assert data["size"] == [2, 2, 1]
    assert data["voxel_count"] == 3
    assert not out.exists()


def test_compile_requires_yes_in_non_tty(home, capsys):
    src = _src(home)
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(home / "t.nbt"), "--input", str(src),
        ]
    )
    assert code == 2
    assert "--yes" in capsys.readouterr().err


def test_compile_writes_nbt(home, capsys):
    src = _src(home)
    out = home / "t.nbt"
    code, data = _compile(home, capsys, "nbt", "t.nbt", src)
    assert code == 0
    assert data["ok"] is True
    assert data["format"] == "nbt"
    assert data["dry_run"] is False
    assert data["data_version"] == 3465
    assert out.exists()
    _, tree = parse_nbt(out.read_bytes())
    grid = parse_structure(tree)
    assert grid.size == (2, 2, 1)
    assert grid.block_count == 3


def test_compile_bom_input_tolerated(home, capsys):
    src = _src(home, bom=True)
    code, data = _compile(home, capsys, "nbt", "bom.nbt", src)
    assert code == 0
    assert data["voxel_count"] == 3


def test_compile_stdin_pipe(home, monkeypatch, capsys):
    out = home / "stdin.nbt"
    original = click.get_text_stream

    def fake(name):
        if name == "stdin":
            return io.StringIO(FIXTURE)
        return original(name)

    monkeypatch.setattr(click, "get_text_stream", fake)
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(out), "--yes", "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["voxel_count"] == 3
    assert out.exists()


def test_compile_invalid_ndjson_exits_3(home, capsys):
    src = _src(home, "{broken\n", name="bad.ndjson")
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(home / "bad.nbt"), "--input", str(src),
            "--yes", "--json",
        ]
    )
    assert code == 3
    err = capsys.readouterr().err
    assert "第 1 行" in err
    assert not (home / "bad.nbt").exists()


def test_compile_duplicate_coords_exits_3(home, capsys):
    src = _src(
        home,
        '{"x":0,"y":0,"z":0,"block":"stone"}\n'
        '{"x":0,"y":0,"z":0,"block":"dirt"}\n',
        name="dup.ndjson",
    )
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(home / "dup.nbt"), "--input", str(src),
            "--yes", "--json",
        ]
    )
    assert code == 3
    assert "重复" in capsys.readouterr().err


def test_compile_bad_origin_exits_2(home, capsys):
    src = _src(home)
    code = main(
        [
            "structure", "compile", "--format", "nbt",
            "-o", str(home / "t.nbt"), "--input", str(src),
            "--origin", "1,2", "--yes", "--json",
        ]
    )
    assert code == 2
    assert "--origin" in capsys.readouterr().err


def test_compile_origin_pads_air(home, capsys):
    src = _src(home, '{"x":5,"y":5,"z":5,"block":"stone"}\n', name="one.ndjson")
    code, data = _compile(
        home, capsys, "nbt", "one.nbt", src, "--origin", "0,0,0"
    )
    assert code == 0
    assert data["size"] == [6, 6, 6]
    grid = parse_structure(parse_nbt((home / "one.nbt").read_bytes())[1])
    assert grid.origin == (0, 0, 0)
    assert grid.block_at(5, 5, 5) == ("minecraft:stone", ())


def test_compile_litematic_with_name(home, capsys):
    src = _src(home)
    out = home / "demo.litematic"
    code = main(
        [
            "structure", "compile", "--format", "litematic",
            "-o", str(out), "--input", str(src),
            "--name", "My Build", "--yes", "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "litematic"
    assert out.exists()

    assert main(["structure", "inspect", str(out), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["format"] == "litematic"
    assert info["version"] == 6
    assert info["name"] == "My Build"
    assert info["enclosing_size"] == [2, 2, 1]


def test_compile_second_write_creates_backup(home, capsys):
    src = _src(home)
    out = home / "t.nbt"
    args = [
        "structure", "compile", "--format", "nbt",
        "-o", str(out), "--input", str(src), "--yes", "--json",
    ]
    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["backup"]
    assert Path(data["backup"]).exists()


# ----------------------------------------------------------------------
# inspect
# ----------------------------------------------------------------------
def test_inspect_nbt_json(home, capsys):
    grid = build_grid(parse_ndjson(FIXTURE), origin=(0, 0, 0), size=(2, 2, 1))
    path = home / "x.nbt"
    path.write_bytes(build_structure(grid))
    code = main(["structure", "inspect", str(path), "--json"])
    assert code == 0
    info = json.loads(capsys.readouterr().out)
    assert info["format"] == "nbt"
    assert info["size"] == [2, 2, 1]
    assert info["block_count"] == 3
    assert info["palette_size"] == 4


def test_inspect_nbt_table_default(home, capsys):
    grid = build_grid(parse_ndjson(FIXTURE), origin=(0, 0, 0), size=(2, 2, 1))
    path = home / "x.nbt"
    path.write_bytes(build_structure(grid))
    code = main(["structure", "inspect", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "nbt" in out


def test_inspect_missing_file_exits_2(capsys):
    code = main(["structure", "inspect", "definitely-missing.nbt", "--json"])
    assert code == 2


def test_global_format_json_before_subcommand(home, capsys):
    grid = build_grid(parse_ndjson(FIXTURE), origin=(0, 0, 0), size=(2, 2, 1))
    path = home / "x.nbt"
    path.write_bytes(build_structure(grid))
    code = main(["--format", "json", "structure", "inspect", str(path)])
    assert code == 0
    info = json.loads(capsys.readouterr().out)
    assert info["format"] == "nbt"


# ----------------------------------------------------------------------
# convert
# ----------------------------------------------------------------------
def test_convert_roundtrip_nbt_litematic_nbt(home, capsys):
    src = _src(home, FLOOR, name="floor.ndjson")
    nbt1 = home / "a.nbt"
    lm = home / "a.litematic"
    nbt2 = home / "b.nbt"

    code, data = _compile(home, capsys, "nbt", "a.nbt", src)
    assert code == 0
    assert data["size"] == [3, 1, 2]

    assert (
        main(
            [
                "structure", "convert", str(nbt1), str(lm),
                "--format", "litematic", "--yes", "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "structure", "convert", str(lm), str(nbt2),
                "--format", "nbt", "--yes", "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    g1 = parse_structure(parse_nbt(nbt1.read_bytes())[1])
    g2 = parse_structure(parse_nbt(nbt2.read_bytes())[1])
    gl = parse_litematic_tree(parse_nbt(lm.read_bytes())[1])
    for other in (g2, gl):
        assert other.size == g1.size == (3, 1, 2)
        assert other.palette == g1.palette
        assert other.cells == g1.cells


def test_convert_requires_yes(home, capsys):
    src = _src(home)
    nbt1 = home / "a.nbt"
    _compile(home, capsys, "nbt", "a.nbt", src)
    code = main(
        [
            "structure", "convert", str(nbt1), str(home / "a.litematic"),
            "--format", "litematic",
        ]
    )
    assert code == 2
    assert "--yes" in capsys.readouterr().err


def test_convert_detects_source_format(home, capsys):
    src = _src(home)
    _compile(home, capsys, "nbt", "a.nbt", src)
    code = main(
        [
            "structure", "convert", str(home / "a.nbt"),
            str(home / "a.litematic"),
            "--format", "litematic", "--yes", "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["from"] == "nbt"
    assert data["to"] == "litematic"


# ----------------------------------------------------------------------
# help
# ----------------------------------------------------------------------
def test_structure_help_lists_commands(capsys):
    code = main(["structure", "--help"])
    assert code == 0
    out = capsys.readouterr().out
    for word in ("compile", "inspect", "convert"):
        assert word in out
