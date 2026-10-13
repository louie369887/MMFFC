"""Phase 7 pack / world import CLI 契约测试（网络层打桩）。"""

from __future__ import annotations

import json
import shutil
import zipfile

import pytest

from mmffc.cli import main

META = json.dumps(
    {"pack": {"pack_format": 15, "description": "CLI pack"}},
    ensure_ascii=False,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def game_dir(tmp_path):
    target = tmp_path / "game"
    (target / "resourcepacks").mkdir(parents=True)
    (target / "shaderpacks").mkdir(parents=True)
    (target / "saves").mkdir(parents=True)
    return target


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _make_rp_zip(path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pack.mcmeta", META)
        archive.writestr("pack.png", b"\x89PNG\r\n\x1a\n")
        archive.writestr("assets/minecraft/textures/block/dirt.png", b"png")
    return path


def _fake_release(kind="resourcepack", tmp_path=None):
    from mmffc.packs.install import PackRelease
    from mmffc.packs.platforms import PackCandidate

    archive = _make_rp_zip(tmp_path / "rp.zip")
    return PackRelease(
        candidate=PackCandidate(
            source="modrinth", project_id="AA", slug="p1", title="Pack1",
            description="d", kind=kind, downloads=3,
        ),
        version_id="v1",
        version_number="1.0.0",
        file_name="rp.zip",
        file_url=f"file://{archive.as_posix()}",
        file_size=archive.stat().st_size,
    )


def _patch_download(monkeypatch, release) -> None:
    def fake_download(self, url, dest):
        shutil.copy2(release.file_url.replace("file://", ""), dest)
        return dest.stat().st_size

    monkeypatch.setattr(
        "mmffc.mods.modrinth.ModrinthClient.download_file", fake_download
    )


# ----------------------------------------------------------------------
# 平台能力
# ----------------------------------------------------------------------
def test_pack_search_modrinth(home, monkeypatch, capsys):
    from mmffc.packs import platforms

    hits = [
        platforms.PackCandidate(
            source="modrinth", project_id="AA", slug="p1", title="Pack1",
            description="d", kind="resourcepack", downloads=3,
        )
    ]
    monkeypatch.setattr("mmffc.packs.install.search_packs", lambda *a, **k: hits)
    assert main(["pack", "search", "faithful", "--type", "resourcepack",
                 "--json"]) == 0
    data = _payload(capsys)
    assert data["kind"] == "resourcepack"
    assert data["kind_label"] == "资源包"
    assert data["count"] == 1
    assert data["results"][0]["slug"] == "p1"


def test_pack_search_requires_type(home, capsys):
    assert main(["pack", "search", "x", "--json"]) == 2
    assert "--type" in capsys.readouterr().err


def test_pack_search_unknown_type(home, capsys):
    assert main(["pack", "search", "x", "--type", "mod", "--json"]) == 3
    assert "未知内容类型" in capsys.readouterr().err


def test_pack_search_curseforge_requires_key(home, capsys, monkeypatch):
    monkeypatch.delenv("CURSEFORGE_API_KEY", raising=False)
    assert main(["pack", "search", "x", "--type", "resourcepack",
                 "--source", "curseforge", "--json"]) == 3
    assert "API Key" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 安装
# ----------------------------------------------------------------------
def test_pack_installs_resourcepack(home, game_dir, tmp_path, monkeypatch, capsys):
    release = _fake_release(tmp_path=tmp_path)
    monkeypatch.setattr("mmffc.cli.pack.resolve_release", lambda *a, **k: release)
    _patch_download(monkeypatch, release)

    assert main(["pack", "install", "p1", "--type", "resourcepack",
                 "--game-dir", str(game_dir), "--yes", "--json"]) == 0
    data = _payload(capsys)
    assert data["ok"] is True
    assert data["target"] == str(game_dir / "resourcepacks")
    assert (game_dir / "resourcepacks" / "assets").is_dir()
    assert (game_dir / "resourcepacks" / "pack.mcmeta").is_file()
    manifest = json.loads(
        (game_dir / "resourcepacks" / ".mmffc-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["entries"]["p1"]["version"] == "1.0.0"


def test_pack_install_dry_run(home, game_dir, tmp_path, monkeypatch, capsys):
    release = _fake_release(tmp_path=tmp_path)
    monkeypatch.setattr("mmffc.cli.pack.resolve_release", lambda *a, **k: release)
    assert main(["pack", "install", "p1", "--type", "resourcepack",
                 "--game-dir", str(game_dir), "--dry-run", "--json"]) == 0
    data = _payload(capsys)
    assert data["dry_run"] is True
    assert not (game_dir / "resourcepacks" / "pack.mcmeta").exists()


def test_pack_install_requires_yes(home, game_dir, tmp_path, monkeypatch, capsys):
    release = _fake_release(tmp_path=tmp_path)
    monkeypatch.setattr("mmffc.cli.pack.resolve_release", lambda *a, **k: release)
    assert main(["pack", "install", "p1", "--type", "resourcepack",
                 "--game-dir", str(game_dir), "--json"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_pack_install_bad_pack_exits_3(home, game_dir, tmp_path, monkeypatch, capsys):
    """下载到内容不符的 zip -> 校验失败退出 3。"""
    from mmffc.packs.install import PackRelease
    from mmffc.packs.platforms import PackCandidate

    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "not a pack")
    release = PackRelease(
        candidate=PackCandidate(
            source="modrinth", project_id="AA", slug="x", title="x",
            description="", kind="resourcepack",
        ),
        version_id="v", version_number="1", file_name="wrong.zip",
        file_url=f"file://{archive.as_posix()}", file_size=0,
    )
    monkeypatch.setattr("mmffc.cli.pack.resolve_release", lambda *a, **k: release)
    _patch_download(monkeypatch, release)
    assert main(["pack", "install", "x", "--type", "resourcepack",
                 "--game-dir", str(game_dir), "--yes", "--json"]) == 3
    assert "内容校验失败" in capsys.readouterr().err


def test_pack_list_empty(home, game_dir, capsys):
    assert main(["pack", "list", "--game-dir", str(game_dir), "--json"]) == 0
    assert _payload(capsys)["count"] == 0


def test_pack_list_finds_installed(home, game_dir, capsys):
    faithful = game_dir / "resourcepacks" / "faithful"
    faithful.mkdir()
    (faithful / "pack.mcmeta").write_text(META, encoding="utf-8")
    datapack = game_dir / "saves" / "newworld" / "datapacks" / "mypack"
    datapack.mkdir(parents=True)
    assert main(["pack", "list", "--game-dir", str(game_dir), "--json"]) == 0
    data = _payload(capsys)
    names = [item["name"] for item in data["items"]]
    assert "faithful" in names
    assert "newworld/mypack" in names


def test_pack_list_requires_game_dir(home, capsys):
    assert main(["pack", "list", "--json"]) == 2


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------
def test_pack_validate_ok(home, game_dir, capsys):
    pack_dir = game_dir / "resourcepacks" / "faithful"
    (pack_dir / "assets" / "minecraft").mkdir(parents=True)
    (pack_dir / "pack.mcmeta").write_text(META, encoding="utf-8")
    assert main(["pack", "validate", str(pack_dir), "--json"]) == 0
    data = _payload(capsys)
    assert data["valid"] is True
    assert data["detected"] == "resourcepack"


def test_pack_validate_fails_on_missing_meta(game_dir, capsys):
    empty = game_dir / "resourcepacks" / "empty"
    empty.mkdir()
    assert main(["pack", "validate", str(empty), "--json"]) == 3
    assert "内容校验失败" in capsys.readouterr().err


def test_pack_validate_missing_dir(home, capsys):
    assert main(["pack", "validate", "no-such-dir", "--json"]) == 2


def test_pack_help_lists_commands(capsys):
    assert main(["pack", "--help"]) == 0
    out = capsys.readouterr().out
    for word in ("search", "install", "list", "validate"):
        assert word in out


# ----------------------------------------------------------------------
# world import
# ----------------------------------------------------------------------
def _world_zip(path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("level.dat", "level-data")
        archive.writestr("region/r.0.0.mca", "mca-data")
    return path


def test_world_import_dry_run(home, tmp_path, capsys):
    archive = _world_zip(tmp_path / "w.zip")
    assert main(["world", "import", str(archive), "--dry-run", "--json"]) == 0
    data = _payload(capsys)
    assert data["dry_run"] is True
    assert data["plan"]["entry_count"] == 2


def test_world_import_ok(home, tmp_path, capsys):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _world_zip(tmp_path / "w.zip")
    assert main(["world", "import", str(archive), "--saves-dir", str(saves),
                 "--yes", "--json"]) == 0
    data = _payload(capsys)
    assert data["name"] == "w"
    assert (saves / "w" / "level.dat").is_file()
    assert (saves / "w" / "region" / "r.0.0.mca").is_file()


def test_world_import_named(home, tmp_path, capsys):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _world_zip(tmp_path / "w.zip")
    assert main(["world", "import", str(archive), "--saves-dir", str(saves),
                 "--name", "myworld", "--yes", "--json"]) == 0
    assert (saves / "myworld" / "level.dat").is_file()


def test_world_import_requires_yes(home, tmp_path, capsys):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _world_zip(tmp_path / "w.zip")
    assert main(["world", "import", str(archive), "--saves-dir", str(saves)]) == 2
    assert "--yes" in capsys.readouterr().err


def test_world_import_rejects_non_world(home, tmp_path, capsys):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = tmp_path / "rp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pack.mcmeta", META)
    assert main(["world", "import", str(archive), "--saves-dir", str(saves),
                 "--yes", "--json"]) == 3
    assert "不是世界存档" in capsys.readouterr().err


def test_world_import_rejects_zip_slip(home, tmp_path, capsys):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "x")
    assert main(["world", "import", str(archive), "--saves-dir", str(saves),
                 "--yes", "--json"]) == 3
    assert "不安全条目" in capsys.readouterr().err
