"""SNBT parser / serializer tests (incl. FTB Quests lenient style)."""

from __future__ import annotations

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats.snbt import snbt_dumps, snbt_loads


def test_compound_basic():
    assert snbt_loads('{a: 1, b: "x"}') == {"a": 1, "b": "x"}


def test_quoted_keys():
    assert snbt_loads('{"a": 1}') == {"a": 1}


def test_bool_and_null_bare_values():
    assert snbt_loads("{flag: true, other: false}") == {
        "flag": True,
        "other": False,
    }


def test_number_types():
    parsed = snbt_loads("{i: 42, d: 2.5, exp: 1e3}")
    assert parsed == {"i": 42, "d": 2.5, "exp": 1000.0}


def test_numeric_suffixes():
    assert snbt_loads("{a: 1L, b: 2.5D, c: 3B, d: 4S}") == {
        "a": 1,
        "b": 2.5,
        "c": 3,
        "d": 4,
    }


def test_lowercase_numeric_suffixes():
    # vanilla SNBT writes byte values as `1b`
    assert snbt_loads("{a: 1b, b: 0b, c: 3.5d}") == {
        "a": 1,
        "b": 0,
        "c": 3.5,
    }


def test_typed_list():
    assert snbt_loads("[I;1,2,3]") == [1, 2, 3]


def test_plain_list():
    assert snbt_loads('["a", "b"]') == ["a", "b"]


def test_nested_structures():
    parsed = snbt_loads(
        '{outer: {inner: [1, {leaf: "v"}]}}'
    )
    assert parsed == {"outer": {"inner": [1, {"leaf": "v"}]}}


def test_string_escapes():
    assert snbt_loads(r'{s: "line\nbreak \"quote\" \\slash"}') == {
        "s": 'line\nbreak "quote" \\slash'
    }


def test_ftb_style_newline_separators():
    # FTB Quests writes one key:value pair per line without commas
    text = """{
  id: "chapter"
  name: "Chapter"
  quests: [
    {
      id: "q1"
      name: "Q1"
    }
  ]
}"""
    assert snbt_loads(text) == {
        "id": "chapter",
        "name": "Chapter",
        "quests": [{"id": "q1", "name": "Q1"}],
    }


def test_dumps_roundtrip():
    obj = {
        "id": "chapter",
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "name": "带引号\"文本",
        "nested": {"list": [1, 2, {"deep": "v"}]},
    }
    parsed = snbt_loads(snbt_dumps(obj))
    assert parsed == obj


def test_dumps_empty_containers():
    assert snbt_dumps({}) == "{}"
    assert snbt_dumps([]) == "[]"


def test_dumps_has_no_trailing_comma():
    text = snbt_dumps({"a": 1, "b": 2})
    assert ",}" not in text.replace(" ", "")


def test_empty_document():
    with pytest.raises(SchemaError):
        snbt_loads("   ")


def test_unclosed_compound():
    with pytest.raises(SchemaError):
        snbt_loads("{a: 1")


def test_missing_separator():
    with pytest.raises(SchemaError):
        snbt_loads("{a: 1 b: 2}")


def test_trailing_content():
    with pytest.raises(SchemaError):
        snbt_loads('{a: 1} extra')


def test_missing_colon():
    with pytest.raises(SchemaError):
        snbt_loads("{a 1}")


def test_dumps_unserializable_type():
    with pytest.raises(SchemaError):
        snbt_dumps({"a": None})
