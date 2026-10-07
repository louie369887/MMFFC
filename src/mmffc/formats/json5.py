"""Minimal JSON5 reader.

Normalizes JSON5 text to strict JSON, then delegates to
:func:`json.loads`. Supported JSON5 extensions:

* ``//`` line comments and ``/* */`` block comments
* trailing commas in objects and arrays
* single-quoted strings
* unquoted identifier keys
* hex numbers, ``Infinity`` / ``-Infinity`` / ``NaN``
* leading/trailing decimal points (``.5``, ``5.``)
"""

from __future__ import annotations

import json
import re
from typing import Any

from mmffc.core.errors import SchemaError

_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_HEX_NUMBER = re.compile(r"[-+]?0[xX][0-9a-fA-F]+")
_DECIMAL = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_KEYWORDS = {"true", "false", "null"}


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _normalize(text: str) -> str:
    """Normalize JSON5 source to strict JSON source."""
    output: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        # --- comments ------------------------------------------------
        if char == "/" and index + 1 < length:
            if text[index + 1] == "/":
                index += 2
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if text[index + 1] == "*":
                index += 2
                while index + 1 < length and not (
                    text[index] == "*" and text[index + 1] == "/"
                ):
                    index += 1
                if index + 1 >= length:
                    raise SchemaError("JSON5: 未闭合的块注释")
                index += 2
                continue

        # --- strings -------------------------------------------------
        if char in ('"', "'"):
            quote = char
            index += 1
            start = index
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                index += 1
            if index >= length:
                raise SchemaError("JSON5: 未闭合的字符串")
            body = text[start:index]
            index += 1
            if quote == "'":
                body = body.replace("\\'", "'").replace('"', '\\"')
            output.append('"' + body + '"')
            continue

        # --- identifiers / keywords / bare numbers -------------------
        if char.isalpha() or char in "_$":
            match = _IDENTIFIER.match(text, index)
            if match:
                word = match.group(0)
                if word in ("Infinity", "NaN"):
                    output.append(word)
                    index = match.end()
                    continue
                lookahead = _skip_space(text, match.end())
                if lookahead < length and text[lookahead] == ":":
                    # unquoted object key
                    output.append('"' + word + '"')
                    index = match.end()
                    continue
                if word in _KEYWORDS:
                    output.append(word)
                    index = match.end()
                    continue
                raise SchemaError(
                    f"JSON5: 无法识别的裸标识符: {word!r}"
                )

        # --- -Infinity -----------------------------------------------
        if char == "-" and index + 1 < length and text[index + 1] == "I":
            match = _IDENTIFIER.match(text, index + 1)
            if match and match.group(0) == "Infinity":
                output.append("-Infinity")
                index = match.end()
                continue

        # --- hex numbers ---------------------------------------------
        hex_match = _HEX_NUMBER.match(text, index)
        if hex_match:
            output.append(str(int(hex_match.group(0), 16)))
            index = hex_match.end()
            continue

        # --- decimals (incl. .5 and 5.) -----------------------------
        decimal_match = _DECIMAL.match(text, index)
        if decimal_match:
            output.append(_fix_decimal(decimal_match.group(0)))
            index = decimal_match.end()
            continue

        output.append(char)
        index += 1

    return "".join(output)


def _fix_decimal(token: str) -> str:
    """Make ``.5`` / ``5.`` strict-JSON safe (``0.5`` / ``5.0``)."""
    if "." not in token:
        return token
    sign = ""
    body = token
    if body[:1] in ("+", "-"):
        sign, body = body[0], body[1:]
    if body.startswith("."):
        body = "0" + body
    if body.endswith("."):
        body += "0"
    return sign + body


def _strip_trailing_commas(text: str) -> str:
    """Remove commas that directly precede '}' or ']' (outside strings)."""
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    output.append(text[index])
                    index += 1
                output.append(text[index])
                index += 1
            if index < length:
                output.append(text[index])
                index += 1
            continue
        if char == ",":
            lookahead = _skip_space(text, index + 1)
            if lookahead < length and text[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def json5_loads(text: str) -> Any:
    """Parse JSON5 text into Python objects."""
    normalized = _strip_trailing_commas(_normalize(text))
    try:
        return json.loads(normalized, parse_constant=_parse_constant)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"JSON5 解析失败: {exc}") from exc


def _parse_constant(name: str) -> Any:
    if name == "Infinity":
        return float("inf")
    if name == "-Infinity":
        return float("-inf")
    if name == "NaN":
        return float("nan")
    raise SchemaError(f"未知的 JSON 常量: {name}")


def json5_dumps(obj: Any, *, indent: int = 2) -> str:
    """Serialize to JSON5 (unquoted identifier keys, trailing commas)."""
    return _render(obj, indent=indent, level=0)


def _is_identifier_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", key))


def _render(obj: Any, *, indent: int, level: int) -> str:
    pad = " " * (indent * level)
    inner_pad = " " * (indent * (level + 1))
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = ["{"]
        items = list(obj.items())
        for index, (key, value) in enumerate(items):
            rendered_key = (
                key if _is_identifier_key(key) else json.dumps(key)
            )
            rendered_value = _render(value, indent=indent, level=level + 1)
            if isinstance(value, (dict, list)):
                lines.append(
                    f"{inner_pad}{rendered_key}:{rendered_value},"
                )
            else:
                lines.append(
                    f"{inner_pad}{rendered_key}: {rendered_value},"
                )
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(
            isinstance(item, (int, float, bool, str)) for item in obj
        ):
            rendered = ", ".join(_scalar(item) for item in obj)
            return f"[{rendered}]"
        lines = ["["]
        for item in obj:
            rendered = _render(item, indent=indent, level=level + 1)
            lines.append(f"{inner_pad}{rendered},")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return _scalar(obj)


def _scalar(obj: Any) -> str:
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return repr(obj)
    raise SchemaError(f"无法序列化为 JSON5 的类型: {type(obj).__name__}")
