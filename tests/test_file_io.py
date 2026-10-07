"""file_io_engine: two-phase validation + atomic write tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmffc.core.errors import ConflictError, SchemaError
from mmffc.io.file_io import FileIOEngine

FIXTURES = Path(__file__).resolve().parent.parent / "tests-data"

VALID_LAYOUT = """type = fancymenu_layout

[layout-meta]
name = Test Menu

[element:button:ok]
x = 10
y = 20
width = 80
height = 20
anchor = center
label = OK
"""

INVALID_LAYOUT = """type = fancymenu_layout

[element:button:bad]
x = nope
anchor = weird
label = Bad
"""

VALID_CHAPTER = """{
  id: "ch1",
  name: "Chapter",
  description: "d",
  icon: "minecraft:book",
  quests: [
    {
      id: "q1",
      name: "Q1",
      description: "d",
      icon: "minecraft:diamond"
    }
  ]
}
"""


@pytest.fixture
def engine(tmp_path: Path) -> FileIOEngine:
    return FileIOEngine(tmp_path / "backups")


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------
def test_validate_valid_fancymenu(engine: FileIOEngine):
    result = engine.validate("fancymenu", VALID_LAYOUT)
    assert result.ok
    assert result.errors == []
    assert result.document is not None


def test_validate_invalid_fancymenu(engine: FileIOEngine):
    result = engine.validate("fancymenu", INVALID_LAYOUT)
    assert not result.ok
    assert any("需要整数" in e for e in result.errors)


def test_validate_parse_error(engine: FileIOEngine):
    result = engine.validate("fancymenu", "[project]\nname = x\n")
    assert not result.ok
    assert result.errors


def test_validate_valid_ftbquests(engine: FileIOEngine):
    result = engine.validate("ftbquests", VALID_CHAPTER)
    assert result.ok
    assert result.errors == []


def test_validate_unknown_target(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.validate("nope", "x")


def test_validate_warning_not_error(engine: FileIOEngine):
    # unknown task type -> forward-compatible warning
    content = VALID_CHAPTER.replace(
        '{\n      id: "q1"',
        '{\n      id: "q1",\n      tasks: [{"type": "future_task"}]',
    )
    result = engine.validate("ftbquests", content)
    assert result.ok
    assert result.warnings


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------
def test_apply_creates_file(engine: FileIOEngine, tmp_path: Path):
    dest = tmp_path / "config"
    result = engine.apply("fancymenu", VALID_LAYOUT, dest)
    assert result.ok
    assert result.path == dest / "test_menu.txt"
    assert result.path.exists()
    assert "fancymenu_layout" in result.path.read_text("utf-8")
    # new file: nothing to back up
    assert result.backup is None


def test_apply_backup_on_overwrite(engine: FileIOEngine, tmp_path: Path):
    dest = tmp_path / "config"
    engine.apply("fancymenu", VALID_LAYOUT, dest)
    second = VALID_LAYOUT.replace("label = OK", "label = Done")
    result = engine.apply("fancymenu", second, dest)
    assert result.ok
    assert result.backup is not None
    assert result.backup.exists()
    # backup preserves the previous content
    assert "label = OK" in result.backup.read_text("utf-8")
    assert "label = Done" in result.path.read_text("utf-8")


def test_apply_dry_run_writes_nothing(
    engine: FileIOEngine, tmp_path: Path
):
    dest = tmp_path / "config"
    result = engine.apply("fancymenu", VALID_LAYOUT, dest, dry_run=True)
    assert result.ok
    assert result.path == dest / "test_menu.txt"
    assert not result.path.exists()


def test_apply_invalid_does_not_write(
    engine: FileIOEngine, tmp_path: Path
):
    dest = tmp_path / "config"
    result = engine.apply("fancymenu", INVALID_LAYOUT, dest)
    assert not result.ok
    assert not dest.exists()


def test_apply_conflict_without_backup(
    engine: FileIOEngine, tmp_path: Path
):
    dest = tmp_path / "config"
    engine.apply("fancymenu", VALID_LAYOUT, dest)
    with pytest.raises(ConflictError):
        engine.apply("fancymenu", VALID_LAYOUT, dest, backup=False)


def test_apply_ftbquests_filename(engine: FileIOEngine, tmp_path: Path):
    dest = tmp_path / "quests"
    result = engine.apply("ftbquests", VALID_CHAPTER, dest)
    assert result.ok
    assert result.path == dest / "ch1.json5"


def test_apply_to_nested_dest(engine: FileIOEngine, tmp_path: Path):
    dest = tmp_path / "a" / "b" / "c"
    result = engine.apply("fancymenu", VALID_LAYOUT, dest)
    assert result.ok
    assert result.path.exists()


# ----------------------------------------------------------------------
# document node access
# ----------------------------------------------------------------------
def test_document_plain_roundtrip(engine: FileIOEngine):
    document = engine.read_document(
        "fancymenu", FIXTURES / "valid_layout.txt"
    )
    plain = engine.document_to_plain("fancymenu", document)
    rebuilt = engine.document_from_plain("fancymenu", plain)
    assert engine.document_to_plain("fancymenu", rebuilt) == plain


def test_get_node(engine: FileIOEngine):
    document = engine.read_document(
        "fancymenu", FIXTURES / "valid_layout.txt"
    )
    plain = engine.document_to_plain("fancymenu", document)
    assert engine.get_node("fancymenu", plain, "type") == "fancymenu_layout"
    assert engine.get_node("fancymenu", plain, "sections.0.name") == "layout-meta"
    assert (
        engine.get_node(
            "fancymenu", plain, "sections.1.properties.anchor"
        )
        == "center"
    )


def test_get_node_missing_key(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.get_node("fancymenu", {"a": 1}, "b")


def test_get_node_bad_index(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.get_node("fancymenu", {"a": [1]}, "a.5")
    with pytest.raises(SchemaError):
        engine.get_node("fancymenu", {"a": [1]}, "a.x")


def test_get_node_scalar_traversal(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.get_node("fancymenu", {"a": 1}, "a.b")


def test_set_node_dict(engine: FileIOEngine):
    updated = engine.set_node("fancymenu", {"a": {"b": 1}}, "a.b", 2)
    assert updated == {"a": {"b": 2}}


def test_set_node_list_index(engine: FileIOEngine):
    updated = engine.set_node(
        "fancymenu", {"items": [1, 2, 3]}, "items.1", 9
    )
    assert updated == {"items": [1, 9, 3]}


def test_set_node_does_not_mutate_original(engine: FileIOEngine):
    original = {"a": {"b": 1}}
    engine.set_node("fancymenu", original, "a.b", 2)
    assert original == {"a": {"b": 1}}


def test_set_node_missing_path(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.set_node("fancymenu", {"a": 1}, "x.y", 2)
    with pytest.raises(SchemaError):
        engine.set_node("fancymenu", {"a": 1}, "", 2)


def test_set_node_out_of_range(engine: FileIOEngine):
    with pytest.raises(SchemaError):
        engine.set_node("fancymenu", {"a": [1]}, "a.4", 2)
