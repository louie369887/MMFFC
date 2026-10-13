"""Phase 7：pack.mcmeta / 结构识别 / 安全解包 / 平台栅格。"""

from __future__ import annotations

import json
import zipfile

import pytest

from mmffc.core.errors import SchemaError
from mmffc.packs.archive import (
    MAX_ENTRIES,
    extract_archive,
    import_world,
    is_archive,
    plan_archive,
)
from mmffc.packs.detect import (
    PACK_DATAPACK,
    PACK_RESOURCEPACK,
    PACK_SHADERPACK,
    PACK_UNKNOWN,
    PACK_WORLD,
    detect_pack_type,
    inspect_pack,
)
from mmffc.packs.meta import TARGET_FORMAT, PackMeta, parse_pack_meta
from mmffc.packs.platforms import (
    CURSEFORGE_CLASS_IDS,
    MODRINTH_PROJECT_TYPES,
    PackCandidate,
    curseforge_class_id,
    install_target,
    modrinth_facets,
    normalize_pack_type,
)

META = json.dumps(
    {"pack": {"pack_format": 15, "description": "测试包"}}, ensure_ascii=False
)


def _make_resourcepack(root) -> None:
    (root / "pack.mcmeta").write_text(META, encoding="utf-8")
    (root / "assets" / "minecraft" / "textures" / "block").mkdir(parents=True)
    (root / "assets" / "minecraft" / "textures" / "block" / "dirt.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
    )
    (root / "pack.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def _make_datapack(root) -> None:
    (root / "pack.mcmeta").write_text(META, encoding="utf-8")
    functions = root / "data" / "mypack" / "function"
    functions.mkdir(parents=True)
    (functions / "hello.mcfunction").write_text("say hi\n", encoding="utf-8")


def _make_shaderpack(root) -> None:
    program = root / "shaders" / "program"
    program.mkdir(parents=True)
    (program / "basic.vsh").write_text("#version 120\n", encoding="utf-8")
    (program / "basic.fsh").write_text("#version 120\n", encoding="utf-8")
    (root / "shaders" / "lib").mkdir()
    (root / "shaders" / "shaders.properties").write_text("oldLighting=false\n")


def _make_world(root) -> None:
    (root / "level.dat").write_bytes(b"level")
    (root / "region").mkdir()
    (root / "region" / "r.0.0.mca").write_bytes(b"mca")


# ----------------------------------------------------------------------
# pack.mcmeta
# ----------------------------------------------------------------------
def test_parse_pack_meta_target():
    meta = parse_pack_meta(META)
    assert meta.found is True
    assert meta.pack_format == TARGET_FORMAT == 15
    assert meta.description == "测试包"
    assert meta.compatible_with_target is True


def test_parse_pack_meta_incompatible():
    meta = parse_pack_meta(json.dumps({"pack": {"pack_format": 48}}))
    assert meta.found is True
    assert meta.compatible_with_target is False
    assert meta.to_dict()["pack_format_versions"]


def test_parse_pack_meta_supported_formats():
    meta = parse_pack_meta(
        json.dumps({"pack": {"pack_format": 15, "supported_formats": [15, 18]}})
    )
    assert meta.supported_formats == [15, 18]


def test_parse_pack_meta_min_max_format():
    meta = parse_pack_meta(
        json.dumps({"pack": {"min_format": 32, "max_format": 41}})
    )
    assert meta.pack_format is None
    assert meta.compatible_with_target is False


def test_parse_pack_meta_bad_json():
    meta = parse_pack_meta("{not json")
    assert meta.found is False
    assert meta.error


def test_parse_pack_meta_missing_pack_section():
    meta = parse_pack_meta(json.dumps({"pack_format": 15}))
    assert meta.error and "pack" in meta.error


# ----------------------------------------------------------------------
# 结构识别
# ----------------------------------------------------------------------
def test_detect_resourcepack(tmp_path):
    root = tmp_path / "rp"
    root.mkdir()
    _make_resourcepack(root)
    assert detect_pack_type(root) == PACK_RESOURCEPACK
    info = inspect_pack(root)
    assert info["valid"] is True
    assert info["type"] == PACK_RESOURCEPACK


def test_detect_datapack(tmp_path):
    root = tmp_path / "dp"
    root.mkdir()
    _make_datapack(root)
    assert detect_pack_type(root) == PACK_DATAPACK
    assert inspect_pack(root)["valid"] is True


def test_detect_shaderpack(tmp_path):
    root = tmp_path / "sp"
    root.mkdir()
    _make_shaderpack(root)
    assert detect_pack_type(root) == PACK_SHADERPACK
    assert inspect_pack(root)["valid"] is True


def test_detect_world(tmp_path):
    root = tmp_path / "save"
    root.mkdir()
    _make_world(root)
    assert detect_pack_type(root) == PACK_WORLD
    assert inspect_pack(root)["valid"] is True


def test_detect_unknown(tmp_path):
    root = tmp_path / "mystery"
    root.mkdir()
    (root / "readme.txt").write_text("nothing", encoding="utf-8")
    assert detect_pack_type(root) == PACK_UNKNOWN
    info = inspect_pack(root)
    assert info["valid"] is False
    assert info["problems"]


def test_detect_from_zip_manifest_mapping():
    mapping = {
        "pack.mcmeta": META.encode("utf-8"),
        "assets/minecraft/textures/block/dirt.png": b"png",
    }
    assert detect_pack_type(mapping) == PACK_RESOURCEPACK


def test_validate_reports_missing_assets(tmp_path):
    root = tmp_path / "broken-rp"
    root.mkdir()
    (root / "pack.mcmeta").write_text(META, encoding="utf-8")
    assert detect_pack_type(root) == PACK_UNKNOWN
    info = inspect_pack(root, pack_type=PACK_RESOURCEPACK)
    assert info["valid"] is False
    assert any("assets/" in item for item in info["problems"])


def test_validate_reports_incompatible_format(tmp_path):
    root = tmp_path / "rp18"
    root.mkdir()
    (root / "pack.mcmeta").write_text(
        json.dumps({"pack": {"pack_format": 48}}), encoding="utf-8"
    )
    (root / "assets" / "minecraft").mkdir(parents=True)
    info = inspect_pack(root)
    assert info["valid"] is False
    assert any("pack_format" in item for item in info["problems"])


def test_validate_shader_without_programs(tmp_path):
    root = tmp_path / "sp-bad"
    root.mkdir()
    (root / "shaders").mkdir()
    info = inspect_pack(root, pack_type=PACK_SHADERPACK)
    assert info["valid"] is False


# ----------------------------------------------------------------------
# 安全解包 / 世界导入
# ----------------------------------------------------------------------
def _zip(path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_is_archive():
    assert is_archive("x.zip") is True
    assert is_archive("x.mcworld") is True
    assert is_archive("x.jar") is False


def test_plan_and_extract_archive(tmp_path):
    archive = _zip(
        tmp_path / "rp.zip",
        {"pack.mcmeta": META.encode("utf-8"), "assets/minecraft/x.png": b"png"},
    )
    plan = plan_archive(archive)
    assert plan.safe is True
    assert set(plan.entries) == {"pack.mcmeta", "assets/minecraft/x.png"}
    dest = tmp_path / "out"
    extract_archive(plan, dest)
    assert (dest / "assets" / "minecraft" / "x.png").read_bytes() == b"png"


def test_plan_rejects_zip_slip(tmp_path):
    archive = _zip(
        tmp_path / "evil.zip", {"../../passwd/bad": b"x", "ok.txt": b"y"}
    )
    plan = plan_archive(archive)
    assert plan.safe is False
    assert any("穿越" in reason for _, reason in plan.rejected)


def test_plan_rejects_absolute_path(tmp_path):
    archive = _zip(tmp_path / "abs.zip", {"C:/Windows/win.ini": b"x"})
    plan = plan_archive(archive)
    assert plan.safe is False
    assert any("绝对路径" in reason for _, reason in plan.rejected)


def test_plan_rejects_non_zip(tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    with pytest.raises(SchemaError, match="不支持的文件类型"):
        plan_archive(plain)


def test_plan_rejects_bad_zip(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(SchemaError, match="损坏"):
        plan_archive(broken)


def test_plan_rejects_too_many_entries(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        for index in range(MAX_ENTRIES + 5):
            zip_file.writestr(f"f{index}.txt", b"x")
    with pytest.raises(SchemaError, match="条目数"):
        plan_archive(archive)


def test_import_world_archive(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _zip(
        tmp_path / "world.zip",
        {"level.dat": b"level", "region/r.0.0.mca": b"mca"},
    )
    result = import_world(archive, saves)
    world_dir = result["world_dir"]
    assert world_dir == saves / "world"
    assert (world_dir / "level.dat").is_file()
    assert (world_dir / "region" / "r.0.0.mca").is_file()


def test_import_mcworld_with_nested_root(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _zip(
        tmp_path / "world.mcworld",
        {"MyWorld/level.dat": b"level", "MyWorld/region/r.0.0.mca": b"mca"},
    )
    result = import_world(archive, saves, name="imported")
    world_dir = result["world_dir"]
    assert world_dir == saves / "imported"
    assert (world_dir / "level.dat").is_file()
    assert (world_dir / "region" / "r.0.0.mca").is_file()


def test_import_world_rejects_non_world(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _zip(tmp_path / "rp.zip", {"pack.mcmeta": META.encode("utf-8")})
    with pytest.raises(SchemaError, match="不是世界存档|未找到"):
        import_world(archive, saves)


def test_import_world_rejects_existing(tmp_path):
    saves = tmp_path / "saves"
    (saves / "world").mkdir(parents=True)
    archive = _zip(
        tmp_path / "world.zip",
        {"level.dat": b"level", "region/r.0.0.mca": b"mca"},
    )
    with pytest.raises(SchemaError, match="已存在"):
        import_world(archive, saves)


def test_import_world_rejects_unsafe_archive(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = _zip(
        tmp_path / "evil.zip", {"../../evil/level.dat": b"level"}
    )
    with pytest.raises(SchemaError, match="不安全"):
        import_world(archive, saves)


# ----------------------------------------------------------------------
# 平台栅格
# ----------------------------------------------------------------------
def test_modrinth_project_types():
    assert MODRINTH_PROJECT_TYPES["resourcepack"] == "resourcepack"
    assert MODRINTH_PROJECT_TYPES["shaderpack"] == "shader"
    assert "datapack" not in MODRINTH_PROJECT_TYPES  # 只能走 all_project_types
    assert "world" not in MODRINTH_PROJECT_TYPES


def test_modrinth_facets_datapack_and_version():
    assert modrinth_facets("datapack", "1.20.1") == [
        ["all_project_types:datapack"],
        ["versions:1.20.1"],
    ]
    assert modrinth_facets("resourcepack", None) == [["project_type:resourcepack"]]


def test_curseforge_class_ids():
    assert curseforge_class_id("resourcepack") == 12
    assert curseforge_class_id("shaderpack") == 6552
    assert curseforge_class_id("world") == 17
    assert curseforge_class_id("datapack") == 6945
    assert curseforge_class_id("modpack") == 4471


def test_install_targets(tmp_path):
    game = tmp_path / ".minecraft"
    assert install_target("resourcepack", game) == game / "resourcepacks"
    assert install_target("shaderpack", game) == game / "shaderpacks"
    assert install_target("datapack", game) == game / "resourcepacks"
    assert install_target("datapack", game, world="w1") == (
        game / "saves" / "w1" / "datapacks"
    )
    assert install_target("world", game) == game / "saves"


def test_normalize_pack_type_rejects_unknown():
    with pytest.raises(SchemaError, match="未知内容类型"):
        normalize_pack_type("mod")


def test_candidate_to_dict():
    candidate = PackCandidate(
        source="modrinth", project_id="abc", slug="pack", title="Pack",
        description="desc", kind="resourcepack", downloads=5,
    )
    data = candidate.to_dict()
    assert data["kind_label"] == "资源包"
    assert data["source"] == "modrinth"
