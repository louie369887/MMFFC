"""Tests for local dependency graph analysis."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from mmffc.mods.deps import analyze_dependencies, missing_dependencies
from mmffc.mods.scanner import scan_mods_dir


def _fabric_jar(path: Path, mod_id: str, depends: dict, recommends: dict | None = None) -> None:
    manifest = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": mod_id.title(),
        "version": "1.0.0",
        "depends": depends,
    }
    if recommends:
        manifest["recommends"] = recommends
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(manifest))


def test_deps_all_present(tmp_path: Path):
    _fabric_jar(tmp_path / "lib.jar", "lib", {})
    _fabric_jar(
        tmp_path / "consumer.jar",
        "consumer",
        {"lib": "*", "minecraft": "~1.20.1"},
    )
    reports = analyze_dependencies(scan_mods_dir(tmp_path))
    by_id = {r.mod_id: r for r in reports}
    assert by_id["consumer"].missing == []
    assert by_id["consumer"].present == ["lib"]
    assert by_id["consumer"].ok


def test_deps_missing(tmp_path: Path):
    _fabric_jar(
        tmp_path / "consumer.jar",
        "consumer",
        {"lib": "*"},
        recommends={"optionaldep": ">=1.0"},
    )
    reports = analyze_dependencies(scan_mods_dir(tmp_path))
    consumer = next(r for r in reports if r.mod_id == "consumer")
    assert consumer.missing == ["lib"]
    assert consumer.optional_missing == ["optionaldep"]
    assert not consumer.ok


def test_missing_dependencies_union(tmp_path: Path):
    _fabric_jar(tmp_path / "a.jar", "a", {"x": "*"})
    _fabric_jar(tmp_path / "b.jar", "b", {"y": "*", "x": "*"})
    missing = missing_dependencies(scan_mods_dir(tmp_path))
    assert missing == ["x", "y"]
