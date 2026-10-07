"""FancyMenu DSL parser / serializer / semantic validator tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmffc.core.errors import SchemaError
from mmffc.formats import fancymenu as fm

FIXTURES = Path(__file__).resolve().parent.parent / "tests-data"

VALID = """type = fancymenu_layout

# a comment line
[layout-meta]
name = Main Menu
random_mode = false

[element:button:play]
x = 100
y = 200
width = 80
height = 20
anchor = center
label = Play
"""


def test_parse_basic():
    doc = fm.parse_layout(VALID)
    assert doc.doc_type == "fancymenu_layout"
    meta = doc.section("layout-meta")
    assert meta is not None
    assert meta.properties["name"] == "Main Menu"
    assert meta.properties["random_mode"] == "false"

    elements = doc.elements()
    assert len(elements) == 1
    element_type, identifier, section = elements[0]
    assert element_type == "button"
    assert identifier == "play"
    assert section.properties["label"] == "Play"
    assert section.properties["x"] == "100"


def test_roundtrip():
    doc = fm.parse_layout(VALID)
    text = fm.serialize_layout(doc)
    doc2 = fm.parse_layout(text)
    assert doc.to_dict() == doc2.to_dict()


def test_missing_type_declaration():
    with pytest.raises(SchemaError):
        fm.parse_layout("[layout-meta]\nname = x\n")


def test_wrong_first_line():
    with pytest.raises(SchemaError):
        fm.parse_layout("foo = bar\n")


def test_property_outside_section():
    with pytest.raises(SchemaError):
        fm.parse_layout("type = fancymenu_layout\nx = 1\n")


def test_property_without_equals():
    with pytest.raises(SchemaError):
        fm.parse_layout("type = fancymenu_layout\n[layout-meta]\nbroken\n")


def test_fixture_valid_layout():
    doc = fm.parse_layout((FIXTURES / "valid_layout.txt").read_text("utf-8"))
    assert fm.validate_layout(doc) == []
    assert len(doc.elements()) == 2


def test_fixture_invalid_layout():
    doc = fm.parse_layout(
        (FIXTURES / "invalid_layout.txt").read_text("utf-8")
    )
    errors = fm.validate_layout(doc)
    assert any("需要整数" in e for e in errors)
    assert any("anchor" in e for e in errors)


def test_validate_ok():
    assert fm.validate_layout(fm.parse_layout(VALID)) == []


def test_validate_bad_anchor():
    text = VALID + "\n[element:text:t1]\nanchor = middle\n"
    errors = fm.validate_layout(fm.parse_layout(text))
    assert any("anchor" in e for e in errors)


def test_validate_bad_int():
    text = VALID + "\n[element:text:t1]\nx = abc\n"
    errors = fm.validate_layout(fm.parse_layout(text))
    assert any("需要整数" in e for e in errors)


def test_validate_negative_int_ok():
    text = VALID + "\n[element:text:t1]\nx = -5\n"
    assert fm.validate_layout(fm.parse_layout(text)) == []


def test_validate_opacity_range():
    text = VALID + "\n[element:text:t1]\nopacity = 2.0\n"
    errors = fm.validate_layout(fm.parse_layout(text))
    assert any("opacity" in e for e in errors)


def test_validate_scale_float_ok():
    text = VALID + "\n[element:text:t1]\nscale = 1.5\n"
    assert fm.validate_layout(fm.parse_layout(text)) == []


def test_validate_unknown_element_type():
    text = VALID + "\n[element:magic:m1]\n"
    errors = fm.validate_layout(fm.parse_layout(text))
    assert any("未知元素类型" in e for e in errors)


def test_validate_bad_section_name():
    text = VALID + "\n[element:button:my id]\n"
    errors = fm.validate_layout(fm.parse_layout(text))
    assert any("非法" in e for e in errors)


def test_validate_wrong_doc_type():
    doc = fm.LayoutDocument(doc_type="other", sections=[])
    errors = fm.validate_layout(doc)
    assert len(errors) == 1
    assert "未知的文档类型" in errors[0]


def test_unknown_property_is_forward_compatible():
    # unknown properties must not produce errors
    text = VALID + "\n[element:text:t1]\nfancy_new_prop = 42\n"
    assert fm.validate_layout(fm.parse_layout(text)) == []


def test_default_filename():
    doc = fm.parse_layout(VALID)
    assert fm.default_filename(doc) == "main_menu.txt"


def test_default_filename_fallback():
    doc = fm.LayoutDocument(doc_type="fancymenu_layout", sections=[])
    assert fm.default_filename(doc) == "layout.txt"


def test_from_plain_roundtrip():
    doc = fm.parse_layout(VALID)
    rebuilt = fm.from_plain(doc.to_dict())
    assert rebuilt.to_dict() == doc.to_dict()
