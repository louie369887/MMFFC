"""JSON5 reader / writer tests."""

from __future__ import annotations

import math

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats.json5 import json5_dumps, json5_loads


def test_line_comments():
    assert json5_loads('{\n  // note\n  a: 1\n}') == {"a": 1}


def test_block_comments():
    assert json5_loads("{\n  /* note\n     more */\n  a: 1\n}") == {
        "a": 1
    }


def test_trailing_commas():
    assert json5_loads('{a: [1, 2,], b: {c: 3,},}') == {
        "a": [1, 2],
        "b": {"c": 3},
    }


def test_single_quoted_strings():
    assert json5_loads("{'a': 'it\\'s'}") == {"a": "it's"}


def test_unquoted_keys():
    assert json5_loads("{foo: 1, _bar: 2, $baz: 3}") == {
        "foo": 1,
        "_bar": 2,
        "$baz": 3,
    }


def test_hex_numbers():
    assert json5_loads("{a: 0x1F, b: -0xff}") == {"a": 31, "b": -255}


def test_infinity_and_nan():
    parsed = json5_loads("{a: Infinity, b: -Infinity, c: NaN}")
    assert parsed["a"] == float("inf")
    assert parsed["b"] == float("-inf")
    assert math.isnan(parsed["c"])


def test_leading_decimal_point():
    assert json5_loads("{a: .5}") == {"a": 0.5}


def test_trailing_decimal_point():
    assert json5_loads("{a: 5.}") == {"a": 5.0}


def test_negative_leading_decimal():
    assert json5_loads("{a: -.25}") == {"a": -0.25}


def test_plain_json_still_works():
    assert json5_loads('{"a": [1, 2], "b": null}') == {
        "a": [1, 2],
        "b": None,
    }


def test_url_in_string_not_a_comment():
    assert json5_loads('{u: "http://example.com"}') == {
        "u": "http://example.com"
    }


def test_bare_identifier_value_rejected():
    with pytest.raises(SchemaError):
        json5_loads("{a: mystery}")


def test_unclosed_block_comment():
    with pytest.raises(SchemaError):
        json5_loads("{/* unterminated")


def test_unclosed_string():
    with pytest.raises(SchemaError):
        json5_loads('{a: "unterminated')


def test_dumps_unquoted_keys_and_trailing_comma():
    text = json5_dumps({"id": "x", "my key": 1})
    assert "\n  id: " in text
    assert '"my key":' in text
    assert text.rstrip().endswith("}")


def test_dumps_roundtrip():
    obj = {
        "id": "chapter",
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "name": "中文名",
        "nested": {"list": [1, 2, {"deep": "v"}]},
        "empty": {},
    }
    assert json5_loads(json5_dumps(obj)) == obj


def test_dumps_empty_containers():
    assert json5_dumps({}) == "{}"
    assert json5_dumps([]) == "[]"
