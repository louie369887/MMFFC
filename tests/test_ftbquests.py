"""FTB Quests chapter format tests (JSON5 / SNBT)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats import ftbquests as fq

FIXTURES = Path(__file__).resolve().parent.parent / "tests-data"

CHAPTER = {
    "id": "ch1",
    "name": "Chapter One",
    "description": "desc",
    "icon": "minecraft:book",
    "quests": [
        {
            "id": "q1",
            "name": "Q1",
            "description": "Get a diamond",
            "icon": "minecraft:diamond",
            "tasks": [{"type": "item", "item": "minecraft:diamond"}],
        }
    ],
}


# ----------------------------------------------------------------------
# parse / detect / serialize
# ----------------------------------------------------------------------
def test_fixture_json5_chapter():
    text = (FIXTURES / "valid_chapter.json5").read_text("utf-8")
    chapter = fq.parse_chapter(text)
    assert chapter["id"] == "mmffc_chapter"
    assert fq.validate_chapter(chapter) == []


def test_serialize_json5_roundtrip():
    text = fq.serialize_chapter(CHAPTER, fmt="json5")
    assert fq.parse_chapter(text, fmt="json5") == CHAPTER


def test_serialize_snbt_roundtrip():
    text = fq.serialize_chapter(CHAPTER, fmt="snbt")
    assert fq.parse_chapter(text, fmt="snbt") == CHAPTER


def test_detect_json5_output():
    text = fq.serialize_chapter(CHAPTER, fmt="json5")
    assert fq.detect_format(text) == "json5"


def test_detect_snbt_output():
    text = fq.serialize_chapter(CHAPTER, fmt="snbt")
    assert fq.detect_format(text) == "snbt"


def test_detect_leading_comment_as_json5():
    assert fq.detect_format("// hi\n{a: 1}") == "json5"
    assert fq.detect_format("/* hi */\n{a: 1}") == "json5"


def test_detect_numeric_suffix_as_snbt():
    assert fq.detect_format('{id: "x"\ncount: 1b}') == "snbt"


def test_detect_typed_list_as_snbt():
    assert fq.detect_format('{ids: [I;1,2,3]}') == "snbt"


def test_detect_snbt_fallback_when_json5_fails():
    # unquoted value parses as SNBT bare token, not JSON5
    text = "{icon: minecraft_book}"
    assert fq.detect_format(text) == "snbt"
    assert fq.parse_chapter(text) == {"icon": "minecraft_book"}


def test_parse_auto_detect_json5_fixture():
    text = (FIXTURES / "valid_chapter.json5").read_text("utf-8")
    chapter = fq.parse_chapter(text)
    assert chapter["id"] == "mmffc_chapter"


def test_parse_non_object_rejected():
    with pytest.raises(SchemaError):
        fq.parse_chapter("[1, 2]")


def test_parse_unknown_format():
    with pytest.raises(SchemaError):
        fq.parse_chapter("{a: 1}", fmt="yaml")


def test_serialize_unknown_format():
    with pytest.raises(SchemaError):
        fq.serialize_chapter(CHAPTER, fmt="yaml")


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------
def test_validate_ok():
    assert fq.validate_chapter(CHAPTER) == []


def test_validate_empty_description_ok():
    chapter = dict(CHAPTER, description="")
    assert fq.validate_chapter(chapter) == []


def test_validate_missing_chapter_fields():
    errors = fq.validate_chapter({"quests": [dict(CHAPTER["quests"][0])]})
    assert any("name" in e for e in errors)
    assert any("icon" in e for e in errors)


def test_validate_missing_description_type():
    chapter = dict(CHAPTER)
    del chapter["description"]
    errors = fq.validate_chapter(chapter)
    assert any("description" in e for e in errors)


def test_validate_chapter_id_pattern():
    chapter = dict(CHAPTER, id="Bad ID!")
    errors = fq.validate_chapter(chapter)
    assert any("id 必须匹配" in e for e in errors)


def test_validate_empty_quests():
    chapter = dict(CHAPTER, quests=[])
    errors = fq.validate_chapter(chapter)
    assert any("quests" in e for e in errors)


def test_validate_quest_missing_fields():
    quest = {"id": "q1"}
    errors = fq.validate_chapter(dict(CHAPTER, quests=[quest]))
    assert any("name" in e for e in errors)
    assert any("description" in e for e in errors)
    assert any("icon" in e for e in errors)


def test_validate_duplicate_quest_ids():
    quests = [
        dict(CHAPTER["quests"][0]),
        dict(CHAPTER["quests"][0]),
    ]
    errors = fq.validate_chapter(dict(CHAPTER, quests=quests))
    assert any("重复" in e for e in errors)


def test_validate_bad_quest_id():
    quest = dict(CHAPTER["quests"][0], id="Q_UPPER")
    errors = fq.validate_chapter(dict(CHAPTER, quests=[quest]))
    assert any("id 必须匹配" in e for e in errors)


def test_validate_dependency_to_missing_quest():
    quest = dict(CHAPTER["quests"][0], dependencies=["ghost"])
    errors = fq.validate_chapter(dict(CHAPTER, quests=[quest]))
    assert any("不存在" in e for e in errors)


def test_validate_unknown_task_type_is_warning():
    quest = dict(
        CHAPTER["quests"][0],
        tasks=[{"type": "mystery_type"}],
    )
    errors = fq.validate_chapter(dict(CHAPTER, quests=[quest]))
    assert errors and all(e.startswith("警告: ") for e in errors)


def test_validate_tasks_must_be_list():
    quest = dict(CHAPTER["quests"][0], tasks="nope")
    errors = fq.validate_chapter(dict(CHAPTER, quests=[quest]))
    assert any("tasks" in e for e in errors)


def test_validate_quest_not_object():
    errors = fq.validate_chapter(dict(CHAPTER, quests=["x"]))
    assert any("必须是对象" in e for e in errors)


# ----------------------------------------------------------------------
# filenames
# ----------------------------------------------------------------------
def test_default_filename_json5():
    assert fq.default_filename(CHAPTER, fmt="json5") == "ch1.json5"


def test_default_filename_snbt():
    assert fq.default_filename(CHAPTER, fmt="snbt") == "ch1.snbt"


def test_default_filename_from_name():
    chapter = {"name": "My Chapter!"}
    assert fq.default_filename(chapter) == "my_chapter.json5"
