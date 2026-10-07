"""SNBT (Stringified NBT) 解析器与序列化器。

FTB Quests 传统上使用 SNBT 作为任务数据的序列化格式。本模块
实现 MMFFC 所需的 SNBT 子集：

* 复合标签 ``{key: value, ...}``
* 列表标签 ``[value, ...]`` 与带类型前缀的列表 ``[I;1,2,3]``
* 字符串 ``"..."``（含转义）
* 数字：int / long(L) / double(D) / float(F) / byte(B) / short(S)，
  后缀大小写均可（``1b`` / ``2L`` / ``3.5d``）
* 布尔：``true`` / ``false``
* 键支持带引号与裸标识符两种形式
"""

from __future__ import annotations

import re
from typing import Any

from mmffc.core.errors import SchemaError


class _Parser:
    """Recursive-descent SNBT parser.

    Lenient about separators: in addition to the standard
    comma, a newline between compound/list entries is
    accepted (FTB Quests writes one ``key:value`` pair
    per line without commas).
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)
        self._saw_newline = False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _skip_ws(self) -> None:
        self._saw_newline = False
        while self.pos < self.length and self.text[self.pos] in " \t\r\n":
            if self.text[self.pos] == "\n":
                self._saw_newline = True
            self.pos += 1

    def _error(self, message: str) -> SchemaError:
        line = self.text.count("\n", 0, self.pos) + 1
        column = self.pos - self.text.rfind("\n", 0, self.pos)
        return SchemaError(f"SNBT 第 {line} 行第 {column} 列: {message}")

    def _peek(self) -> str | None:
        self._skip_ws()
        if self.pos >= self.length:
            return None
        return self.text[self.pos]

    def _separator(self, closing: str) -> None:
        """Validate the separator between two entries."""
        char = self._peek()
        if char is None:
            raise self._error(f"未闭合 (缺少 {closing!r})")
        if char == ",":
            self.pos += 1
            return
        if char == closing:
            return
        if self._saw_newline:
            # lenient: newline acts as separator
            return
        raise self._error(
            f"值之后需要 ',' 或 {closing!r} (得到 {char!r})"
        )

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def parse(self) -> Any:
        self._skip_ws()
        if self.pos >= self.length:
            raise self._error("空的 SNBT 文档")
        value = self._value()
        self._skip_ws()
        if self.pos < self.length:
            raise self._error(
                f"值之后存在多余内容: {self.text[self.pos:self.pos + 20]!r}"
            )
        return value

    # ------------------------------------------------------------------
    # grammar
    # ------------------------------------------------------------------
    def _value(self) -> Any:
        char = self._peek()
        if char is None:
            raise self._error("意外的输入结束")
        if char == "{":
            return self._compound()
        if char == "[":
            return self._list()
        if char == '"':
            return self._string()
        if char in "-+0123456789.":
            return self._number()
        return self._bare()

    def _compound(self) -> dict[str, Any]:
        self.pos += 1  # consume '{'
        result: dict[str, Any] = {}
        while True:
            char = self._peek()
            if char is None:
                raise self._error("复合标签未闭合 (缺少 '}')")
            if char == "}":
                self.pos += 1
                return result
            key = self._key()
            sep = self._peek()
            if sep != ":":
                raise self._error(f"键 {key!r} 之后需要 ':'")
            self.pos += 1
            result[key] = self._value()
            self._separator("}")

    def _key(self) -> str:
        char = self._peek()
        if char == '"':
            return self._string()
        match = re.match(r"[A-Za-z_][A-Za-z0-9_.-]*", self.text[self.pos :])
        if not match:
            raise self._error("非法的键")
        self.pos += match.end()
        return match.group(0)

    def _list(self) -> list[Any]:
        self.pos += 1  # consume '['
        char = self._peek()
        if char is None:
            raise self._error("列表未闭合 (缺少 ']')")
        # typed list, e.g. [I;1,2,3]
        typed = re.match(r"([BSDLFI]);", self.text[self.pos :])
        if typed:
            self.pos += typed.end()
        result: list[Any] = []
        while True:
            char = self._peek()
            if char is None:
                raise self._error("列表未闭合 (缺少 ']')")
            if char == "]":
                self.pos += 1
                return result
            result.append(self._value())
            self._separator("]")

    def _string(self) -> str:
        self.pos += 1  # consume '"'
        chars: list[str] = []
        while self.pos < self.length:
            char = self.text[self.pos]
            if char == '"':
                self.pos += 1
                return "".join(chars)
            if char == "\\":
                self.pos += 1
                if self.pos >= self.length:
                    raise self._error("字符串末尾的悬空转义符")
                escaped = self.text[self.pos]
                escapes = {
                    "n": "\n",
                    "t": "\t",
                    "r": "\r",
                    "b": "\b",
                    "f": "\f",
                    '"': '"',
                    "\\": "\\",
                    "'": "'",
                }
                chars.append(escapes.get(escaped, escaped))
                self.pos += 1
                continue
            chars.append(char)
            self.pos += 1
        raise self._error("字符串未闭合")

    def _number(self) -> Any:
        match = re.match(
            r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[BbSsDdLlFf]?",
            self.text[self.pos :],
        )
        if not match:
            raise self._error("非法的数字")
        token = match.group(0)
        self.pos += match.end()
        last = token[-1]
        suffix = last.upper() if last in "BbSsDdLlFf" else ""
        body = token[: len(token) - 1] if suffix else token
        try:
            if suffix in ("D", "F"):
                return float(body)
            if suffix in ("L", "B", "S"):
                return int(body)
            if "." in body or "e" in body or "E" in body:
                return float(body)
            return int(body)
        except ValueError:
            raise self._error(f"非法的数字: {token!r}") from None

    def _bare(self) -> Any:
        match = re.match(r"[A-Za-z_][A-Za-z0-9_.+-]*", self.text[self.pos :])
        if not match:
            raise self._error("无法识别的值")
        token = match.group(0)
        self.pos += match.end()
        if token == "true":
            return True
        if token == "false":
            return False
        return token


def snbt_loads(text: str) -> Any:
    """Parse an SNBT document into Python objects."""
    return _Parser(text).parse()


def _escape_string(value: str) -> str:
    escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\n": "\\n",
        "\t": "\\t",
        "\r": "\\r",
    }
    return '"' + "".join(escapes.get(c, c) for c in value) + '"'


def snbt_dumps(obj: Any, *, indent: int = 2, _level: int = 0) -> str:
    """Serialize Python objects to pretty-printed SNBT."""
    pad = " " * (indent * _level)
    inner_pad = " " * (indent * (_level + 1))
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = ["{"]
        items = list(obj.items())
        for index, (key, value) in enumerate(items):
            comma = "," if index < len(items) - 1 else ""
            rendered = snbt_dumps(value, indent=indent, _level=_level + 1)
            if isinstance(value, (dict, list)):
                lines.append(
                    f"{inner_pad}{_escape_string(str(key))}:{rendered}{comma}"
                )
            else:
                lines.append(
                    f"{inner_pad}{_escape_string(str(key))}: {rendered}{comma}"
                )
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(isinstance(item, (int, float, bool, str)) for item in obj):
            rendered = ", ".join(_scalar(item) for item in obj)
            return f"[{rendered}]"
        lines = ["["]
        for index, item in enumerate(obj):
            comma = "," if index < len(obj) - 1 else ""
            rendered = snbt_dumps(item, indent=indent, _level=_level + 1)
            lines.append(f"{inner_pad}{rendered}{comma}")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return _scalar(obj)


def _scalar(obj: Any) -> str:
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, str):
        return _escape_string(obj)
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return repr(obj)
    raise SchemaError(f"无法序列化为 SNBT 的类型: {type(obj).__name__}")
