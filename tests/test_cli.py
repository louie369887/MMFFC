"""CLI contract tests: exit codes, global options, pipes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from mmffc.cli import main

FIXTURES = Path(__file__).resolve().parent.parent / "tests-data"


def test_version(capsys):
    code = main(["--version"])
    assert code == 0
    assert "mmffc" in capsys.readouterr().out


def test_help(capsys):
    code = main(["--help"])
    assert code == 0
    out = capsys.readouterr().out
    assert "mod" in out
    assert "shell" in out


def test_mod_group_help(capsys):
    code = main(["mod", "--help"])
    assert code == 0
    out = capsys.readouterr().out
    assert "search" in out
    assert "install" in out


def test_unknown_command_exits_2(capsys):
    code = main(["definitely-not-a-command"])
    assert code == 2


def test_json_and_ndjson_mutually_exclusive(capsys):
    code = main(["--json", "--ndjson", "mod", "search", "x"])
    assert code == 2


def test_global_options_after_subcommand(capsys):
    code = main(["mod", "search", "--help"])
    assert code == 0
    out = capsys.readouterr().out
    assert "--ndjson" in out


@responses.activate
def test_mod_search_json(capsys):
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={
            "total_hits": 1,
            "hits": [
                {
                    "project_id": "AANobbMI",
                    "slug": "sodium",
                    "title": "Sodium",
                    "description": "fast",
                    "downloads": 5,
                    "follows": 1,
                    "categories": ["fabric"],
                    "author": "jellysquid3",
                    "date_created": "2021-01-03T00:53:34.185936+00:00",
                    "date_modified": "2026-01-03T00:53:34.185936+00:00",
                    "icon_url": "https://example.com/icon.png",
                }
            ],
        },
    )
    code = main(["mod", "search", "sodium", "--limit", "1", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["slug"] == "sodium"


@responses.activate
def test_mod_search_ndjson_streaming(capsys):
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={
            "total_hits": 2,
            "hits": [
                {"project_id": "1", "slug": "a", "title": "A"},
                {"project_id": "2", "slug": "b", "title": "B"},
            ],
        },
    )
    code = main(["mod", "search", "--ndjson", "--limit", "2"])
    assert code == 0
    lines = [
        line for line in capsys.readouterr().out.splitlines() if line.strip()
    ]
    assert len(lines) == 2
    assert json.loads(lines[0])["slug"] == "a"


@responses.activate
def test_mod_search_table(capsys):
    responses.get(
        "https://api.modrinth.com/v2/search",
        json={
            "total_hits": 1,
            "hits": [
                {
                    "project_id": "1",
                    "slug": "a",
                    "title": "Alpha",
                    "description": "d",
                    "downloads": 1,
                    "follows": 1,
                }
            ],
        },
    )
    code = main(["mod", "search", "a", "--no-color"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Alpha" in out


def test_install_requires_yes_in_non_tty(capsys):
    # pytest replaces stdin/stdout with non-tty objects
    code = main(["mod", "install", "sodium"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--yes" in err


@responses.activate
def test_install_dry_run_needs_no_yes(capsys):
    responses.get(
        "https://api.modrinth.com/v2/project/sodium",
        json={"id": "AANobbMI", "slug": "sodium", "title": "Sodium"},
    )
    responses.get(
        "https://api.modrinth.com/v2/project/AANobbMI/version",
        json=[
            {
                "id": "V1",
                "name": "v1",
                "version_number": "1.0.0",
                "version_type": "release",
                "game_versions": ["1.20.1"],
                "loaders": ["fabric"],
                "files": [
                    {
                        "filename": "sodium.jar",
                        "url": "https://cdn.example.com/sodium.jar",
                        "size": 10,
                        "primary": True,
                    }
                ],
                "dependencies": [],
                "date_published": "2026-01-01T00:00:00+00:00",
            }
        ],
    )
    code = main(
        [
            "mod",
            "install",
            "sodium",
            "--dry-run",
            "--json",
            "--mods-dir",
            "tests-data-mods",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["plans"][0]["install"][0]["slug"] == "sodium"


def test_config_validate_valid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    code = main(
        [
            "config",
            "validate",
            "fancymenu",
            str(FIXTURES / "valid_layout.txt"),
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True


def test_config_validate_invalid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    code = main(
        [
            "config",
            "validate",
            "fancymenu",
            str(FIXTURES / "invalid_layout.txt"),
            "--json",
        ]
    )
    assert code == 3
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["errors"]


def test_config_validate_parse_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    # pyproject.toml is not a FancyMenu layout
    code = main(
        [
            "config",
            "validate",
            "fancymenu",
            "pyproject.toml",
            "--json",
        ]
    )
    assert code == 3


def test_config_apply_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    dest = tmp_path / "cfg"
    code = main(
        [
            "config",
            "apply",
            "fancymenu",
            str(FIXTURES / "valid_layout.txt"),
            "--dest",
            str(dest),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert not dest.exists()


def test_config_apply_requires_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    code = main(
        [
            "config",
            "apply",
            "fancymenu",
            str(FIXTURES / "valid_layout.txt"),
            "--dest",
            str(tmp_path / "cfg"),
        ]
    )
    assert code == 2
    assert "--yes" in capsys.readouterr().err


def test_config_cli_workflow(tmp_path, monkeypatch, capsys):
    """apply -> get -> set -> diff end to end."""
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    fixture = str(FIXTURES / "valid_layout.txt")

    # apply
    code = main(
        [
            "config",
            "apply",
            "fancymenu",
            fixture,
            "--dest",
            str(tmp_path / "cfg"),
            "--yes",
            "--json",
        ]
    )
    assert code == 0
    applied = json.loads(capsys.readouterr().out)
    path = Path(applied["path"])
    assert path.exists()
    assert applied["ok"] is True

    # get
    code = main(
        [
            "config",
            "get",
            "fancymenu",
            "sections.1.properties.anchor",
            "--dest",
            str(path),
            "--json",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == "center"

    # set (creates a backup of the previous version)
    code = main(
        [
            "config",
            "set",
            "fancymenu",
            "sections.1.properties.anchor",
            "bottom_left",
            "--dest",
            str(path),
            "--yes",
            "--json",
        ]
    )
    assert code == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["ok"] is True
    assert updated["backup"]

    # diff (fixture anchor=center, file now bottom_left)
    code = main(
        [
            "config",
            "diff",
            "fancymenu",
            fixture,
            "--dest",
            str(path),
            "--json",
        ]
    )
    assert code == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["same"] is False
    assert diff["count"] >= 1

    # get on a missing path -> usage error
    code = main(
        [
            "config",
            "get",
            "fancymenu",
            "sections.99",
            "--dest",
            str(path),
            "--json",
        ]
    )
    assert code == 1


def test_cache_info(capsys):
    code = main(["cache", "info", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert all("path" in row and "bytes" in row for row in data)


def test_cache_clean_without_target_exits_2(capsys):
    code = main(["cache", "clean"])
    assert code == 2
