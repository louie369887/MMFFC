"""Tests for the install manifest."""

from __future__ import annotations

import json
from pathlib import Path

from mmffc.mods.manifest import InstallManifest


def test_roundtrip(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add(
        {
            "project_id": "AANobbMI",
            "slug": "sodium",
            "name": "Sodium",
            "version": "mc1.20.1-0.5.13-fabric",
            "version_id": "OihdIimA",
            "file": "sodium-fabric-0.5.13+mc1.20.1.jar",
        }
    )
    manifest.save()

    reloaded = InstallManifest.load(mods_dir)
    entry = reloaded.get("sodium")
    assert entry is not None
    assert entry["version"] == "mc1.20.1-0.5.13-fabric"
    assert reloaded.has("sodium")
    assert len(reloaded.all()) == 1


def test_find_by_various_refs(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add(
        {
            "project_id": "AANobbMI",
            "slug": "sodium",
            "name": "Sodium",
            "version": "v1",
            "file": "sodium.jar",
        }
    )
    manifest.save()

    reloaded = InstallManifest.load(mods_dir)
    assert reloaded.find("sodium") is not None
    assert reloaded.find("SODIUM")[0] == "sodium"
    assert reloaded.find("AANobbMI")[0] == "sodium"
    assert reloaded.find("sodium.jar")[0] == "sodium"
    assert reloaded.find("nonexistent") is None


def test_remove(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add({"slug": "sodium", "file": "sodium.jar", "version": "v1"})
    manifest.save()

    reloaded = InstallManifest.load(mods_dir)
    removed = reloaded.remove("sodium")
    assert removed is not None
    assert not reloaded.has("sodium")
    reloaded.save()
    assert InstallManifest.load(mods_dir).all() == []


def test_corrupt_manifest_raises(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / ".mmffc-manifest.json").write_text("{not json")
    from mmffc.core.errors import IntegrityError

    try:
        InstallManifest.load(mods_dir)
    except IntegrityError:
        pass
    else:
        raise AssertionError("expected IntegrityError")


def test_manifest_file_is_valid_json(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add({"slug": "x", "file": "x.jar"})
    manifest.save()
    data = json.loads(
        (mods_dir / ".mmffc-manifest.json").read_text(encoding="utf-8")
    )
    assert data["version"] == 1
    assert "x" in data["entries"]
