"""Tests for the local mods/ scanner."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from mmffc.mods.scanner import (
    parse_jar,
    scan_mods_dir,
)


def make_fabric_jar(
    path: Path,
    mod_id: str = "testmod",
    version: str = "1.0.0",
    depends: dict | None = None,
) -> None:
    manifest = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": "Test Mod",
        "version": version,
        "depends": depends
        or {"fabricloader": ">=0.15.0", "minecraft": "~1.20.1"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(manifest))


def make_forge_jar(
    path: Path,
    mod_id: str = "testmod",
    version: str = "1.0.0",
    deps: dict | None = None,
) -> None:
    toml = (
        'modLoader="javafml"\n'
        'loaderVersion="[36,)"\n\n'
        "[[mods]]\n"
        f'modId="{mod_id}"\n'
        f'version="{version}"\n'
        'displayName="Test Mod"\n'
    )
    if deps:
        toml += f"\n[dependencies.{mod_id}]\n"
        for dep_id, spec in deps.items():
            toml += (
                f'"{dep_id}" = {{ versionRange="{spec}", '
                f'side="BOTH", optional=false }}\n'
            )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/mods.toml", toml)


def test_parse_fabric_jar(tmp_path: Path):
    jar = tmp_path / "testmod.jar"
    make_fabric_jar(jar)
    mod_file = parse_jar(jar)
    assert mod_file.loader == "fabric"
    assert mod_file.error is None
    primary = mod_file.primary
    assert primary is not None
    assert primary.mod_id == "testmod"
    assert primary.version == "1.0.0"
    assert primary.name == "Test Mod"
    dep_ids = {d.mod_id for d in primary.dependencies}
    assert dep_ids == {"fabricloader", "minecraft"}


def test_parse_forge_jar(tmp_path: Path):
    jar = tmp_path / "testmod.jar"
    make_forge_jar(jar, deps={"jei": "[1.0,)"}, version="2.0.0")
    mod_file = parse_jar(jar)
    assert mod_file.loader == "forge"
    primary = mod_file.primary
    assert primary is not None
    assert primary.mod_id == "testmod"
    assert primary.version == "2.0.0"
    assert len(primary.dependencies) == 1
    dep = primary.dependencies[0]
    assert dep.mod_id == "jei"
    assert dep.version == "[1.0,)"
    assert dep.optional is False


def test_parse_invalid_jar(tmp_path: Path):
    jar = tmp_path / "broken.jar"
    jar.write_bytes(b"not a zip")
    mod_file = parse_jar(jar)
    assert mod_file.error is not None
    assert mod_file.loader is None


def test_scan_mods_dir(tmp_path: Path):
    make_fabric_jar(tmp_path / "a.jar", mod_id="alpha")
    make_forge_jar(tmp_path / "b.jar", mod_id="beta")
    (tmp_path / "readme.txt").write_text("ignored")
    files = scan_mods_dir(tmp_path)
    assert [f.filename for f in files] == ["a.jar", "b.jar"]
    assert {f.primary.mod_id for f in files} == {"alpha", "beta"}


def test_scan_missing_dir(tmp_path: Path):
    assert scan_mods_dir(tmp_path / "nope") == []
