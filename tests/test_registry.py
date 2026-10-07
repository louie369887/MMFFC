"""Tests for install planning and version selection (mocked HTTP)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mmffc.core.errors import DependencyError, NotFoundError
from mmffc.mods.manifest import InstallManifest
from mmffc.mods.modrinth import ModrinthClient
from mmffc.mods.registry import plan_install


class FakeModrinthClient(ModrinthClient):
    """Offline Modrinth client driven by canned fixtures."""

    def __init__(self, projects: dict[str, dict], versions: dict[str, list]) -> None:
        self._projects = projects
        self._versions = versions
        # bypass network setup
        import requests

        self.session = requests.Session()
        self.timeout = 1

    def get_project(self, id_or_slug: str) -> dict[str, Any]:
        for project in self._projects.values():
            if id_or_slug in (project["slug"], project["id"]):
                return project
        raise NotFoundError(f"not found: {id_or_slug}")

    def get_versions(self, project_id: str) -> list[dict[str, Any]]:
        if project_id not in self._versions:
            raise DependencyError(f"no versions: {project_id}")
        return self._versions[project_id]


def _version(
    vid: str,
    number: str,
    *,
    vtype: str = "release",
    games: list[str] | None = None,
    loaders: list[str] | None = None,
    deps: list[dict] | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    return {
        "id": vid,
        "name": number,
        "version_number": number,
        "version_type": vtype,
        "game_versions": games or ["1.20.1"],
        "loaders": loaders or ["fabric"],
        "files": [
            {
                "filename": filename or f"mod-{vid}.jar",
                "url": f"https://cdn.example.com/{vid}.jar",
                "size": 100,
                "primary": True,
            }
        ],
        "dependencies": deps or [],
        "date_published": "2026-01-01T00:00:00+00:00",
    }


SODIUM = {"id": "AANobbMI", "slug": "sodium", "title": "Sodium"}
LIB = {"id": "LIB00001", "slug": "lib", "title": "Lib"}


@pytest.fixture()
def client() -> FakeModrinthClient:
    return FakeModrinthClient(
        projects={"sodium": SODIUM, "lib": LIB},
        versions={
            SODIUM["id"]: [
                _version("V1", "1.0.0", vtype="alpha"),
                _version("V2", "2.0.0", vtype="beta"),
                _version(
                    "V3",
                    "3.0.0",
                    deps=[
                        {
                            "project_id": LIB["id"],
                            "dependency_type": "required",
                        }
                    ],
                ),
                _version(
                    "V4",
                    "4.0.0",
                    games=["1.21"],
                    loaders=["neoforge"],
                ),
            ],
            LIB["id"]: [_version("LV1", "1.0.0")],
        },
    )


def test_pick_latest_release(client):
    version = client.pick_version(SODIUM["id"])
    assert version["version_number"] == "3.0.0"


def test_pick_respects_constraints(client):
    version = client.pick_version(
        SODIUM["id"], mc_version="1.20.1", loader="fabric"
    )
    assert version["version_number"] == "3.0.0"


def test_pick_no_match_raises(client):
    with pytest.raises(DependencyError):
        client.pick_version(
            SODIUM["id"], mc_version="1.19.2", loader="fabric"
        )


def test_pick_exact_version(client):
    version = client.pick_version(SODIUM["id"], version_number="2.0.0")
    assert version["version_number"] == "2.0.0"


def test_plan_without_constraints(client, tmp_path: Path):
    plan = plan_install(client, "sodium")
    assert len(plan.items) == 1
    assert plan.items[0].slug == "sodium"
    assert plan.items[0].version_number == "3.0.0"


def test_plan_skips_installed_version(client, tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add(
        {
            "project_id": SODIUM["id"],
            "slug": "sodium",
            "version": "3.0.0",
            "file": "mod-V3.jar",
        }
    )
    manifest.save()
    plan = plan_install(client, "sodium", mods_dir=mods_dir)
    assert plan.items == []
    assert plan.skipped[0]["reason"] == "already installed"


def test_plan_detects_conflict(client, tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    manifest = InstallManifest.load(mods_dir)
    manifest.add(
        {
            "project_id": SODIUM["id"],
            "slug": "sodium",
            "version": "1.0.0",
            "file": "mod-V1.jar",
        }
    )
    manifest.save()
    plan = plan_install(client, "sodium", mods_dir=mods_dir)
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0]["incoming_version"] == "3.0.0"
    # the item is still planned (caller decides to force or abort)
    assert len(plan.items) == 1


def test_plan_resolve_deps(client, tmp_path: Path):
    plan = plan_install(
        client, "sodium", resolve_deps=True, mods_dir=tmp_path / "mods"
    )
    slugs = [(item.slug, item.required_by) for item in plan.items]
    assert ("sodium", None) in slugs
    assert ("lib", "sodium") in slugs


def test_plan_resolve_deps_cycle_safe(client, tmp_path: Path):
    # sodium depends on lib; make lib depend back on sodium
    client._versions[LIB["id"]] = [
        _version(
            "LV1",
            "1.0.0",
            deps=[
                {
                    "project_id": SODIUM["id"],
                    "dependency_type": "required",
                }
            ],
        )
    ]
    plan = plan_install(
        client, "sodium", resolve_deps=True, mods_dir=tmp_path / "mods"
    )
    assert len({item.slug for item in plan.items}) == 2


def test_plan_unknown_project(client):
    from mmffc.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        plan_install(client, "does-not-exist", mods_dir=None)
