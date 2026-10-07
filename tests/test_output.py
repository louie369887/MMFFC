"""Tests for output rendering (CLI.md 2.2)."""

from __future__ import annotations

import json

from mmffc.core.output import (
    OutputFormat,
    OutputRenderer,
    format_json,
    format_ndjson,
)


def test_format_json():
    assert json.loads(format_json({"a": 1})) == {"a": 1}


def test_format_ndjson():
    text = format_ndjson([{"a": 1}, {"b": 2}])
    lines = text.strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}


def test_renderer_json(capsys):
    renderer = OutputRenderer(OutputFormat.JSON)
    renderer.emit({"a": 1})
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_renderer_ndjson(capsys):
    renderer = OutputRenderer(OutputFormat.NDJSON)
    renderer.emit([{"a": 1}, {"b": 2}])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2


def test_renderer_csv(capsys):
    renderer = OutputRenderer(OutputFormat.CSV)
    renderer.emit([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "a,b"
    assert out.splitlines()[1] == "1,x"


def test_renderer_tsv(capsys):
    renderer = OutputRenderer(OutputFormat.TSV)
    renderer.emit([{"a": 1}])
    assert capsys.readouterr().out.splitlines()[0] == "a"


def test_renderer_raw_string(capsys):
    renderer = OutputRenderer(OutputFormat.RAW)
    renderer.emit("hello")
    assert capsys.readouterr().out == "hello"


def test_renderer_table_dict(capsys):
    renderer = OutputRenderer(OutputFormat.TABLE, color=False)
    renderer.emit({"a": 1, "b": "x"})
    out = capsys.readouterr().out
    assert "a" in out and "x" in out


def test_renderer_table_empty_list(capsys):
    renderer = OutputRenderer(OutputFormat.TABLE, color=False)
    renderer.emit([])
    assert capsys.readouterr().out == ""


def test_renderer_nested_dict_falls_back_to_json(capsys):
    renderer = OutputRenderer(OutputFormat.TABLE, color=False)
    renderer.emit({"a": {"nested": True}})
    assert json.loads(capsys.readouterr().out) == {"a": {"nested": True}}
