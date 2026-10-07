"""FancyMenu 布局 DSL 的解析器 / 序列化器 / 语义校验器。

FancyMenu 使用自定义的类 INI 文本格式（非 JSON），布局文件以
``type = fancymenu_layout`` 开头，随后是由 ``[...]`` 包裹的节与
``key = value`` 属性。元素节形如 ``[element:<type>:<identifier>]``。

本模块实现 MMFFC 支持的子集：
* 文档类型声明（仅支持 fancymenu_layout）
* 节名语法：``[a-z0-9_-]+(:[a-z0-9_-]+)*``
* 已知元素类型与核心属性的语义校验
* 未知属性/节前向兼容（仅警告，不报错）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mmffc.core.errors import SchemaError

DOC_TYPE = "fancymenu_layout"

# Section names: lowercase words separated by colons.
SECTION_NAME_RE = re.compile(r"^[a-z0-9_-]+(?::[a-z0-9_-]+)*$")
# Element identifiers.
IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]+$")

# FancyMenu v3 element types supported by MMFFC validation.
ELEMENT_TYPES = {
    "button",
    "text",
    "image",
    "texture",
    "rectangle",
    "item",
    "scrollable",
    "tooltip",
    "sound",
    "empty",
}

# Anchors accepted by FancyMenu layout elements.
ANCHORS = {
    "top_left",
    "top_center",
    "top_right",
    "center_left",
    "center",
    "center_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
}

# Integer-valued properties shared by layout elements.
INT_PROPERTIES = {"x", "y", "width", "height", "z_index"}
# Float-valued properties (0.0 - 1.0 range checked semantically).
FLOAT_PROPERTIES = {"opacity", "scale"}
# Enum-valued properties.
ENUM_PROPERTIES = {"anchor": ANCHORS}


@dataclass
class Section:
    """One ``[name]`` block with its ordered properties."""

    name: str
    properties: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "properties": dict(self.properties)}


@dataclass
class LayoutDocument:
    """Parsed FancyMenu layout document."""

    doc_type: str
    sections: list[Section] = field(default_factory=list)

    def section(self, name: str) -> Section | None:
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def elements(self) -> list[tuple[str, str, Section]]:
        """Return (element_type, identifier, section) triples."""
        result = []
        for section in self.sections:
            parts = section.name.split(":")
            if len(parts) == 3 and parts[0] == "element":
                result.append((parts[1], parts[2], section))
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.doc_type,
            "sections": [s.to_dict() for s in self.sections],
        }


def _split_property(line: str, lineno: int) -> tuple[str, str]:
    key, sep, value = line.partition("=")
    if not sep:
        raise SchemaError(f"第 {lineno} 行: 无效的属性行 (缺少 '='): {line}")
    key = key.strip()
    if not key:
        raise SchemaError(f"第 {lineno} 行: 属性名为空")
    return key, value.strip()


def parse_layout(text: str) -> LayoutDocument:
    """Parse FancyMenu DSL text into a LayoutDocument."""
    doc_type: str | None = None
    sections: list[Section] = []
    current: Section | None = None

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if doc_type is None:
            key, value = _split_property(line, lineno)
            if key != "type":
                raise SchemaError(
                    f"第 {lineno} 行: FancyMenu 文档必须以 "
                    f"'type = {DOC_TYPE}' 开头 (得到 {key!r})"
                )
            doc_type = value
            continue

        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if not name:
                raise SchemaError(f"第 {lineno} 行: 空的节名")
            current = Section(name)
            sections.append(current)
            continue

        if current is None:
            raise SchemaError(
                f"第 {lineno} 行: 属性出现在任何节之外: {line}"
            )
        key, value = _split_property(line, lineno)
        current.properties[key] = value

    if doc_type is None:
        raise SchemaError("缺少 type 声明 (文档必须以 'type = ...' 开头)")
    return LayoutDocument(doc_type=doc_type, sections=sections)


def serialize_layout(doc: LayoutDocument) -> str:
    """Serialize a LayoutDocument back to FancyMenu DSL text."""
    lines = [f"type = {doc.doc_type}"]
    for section in doc.sections:
        lines.append("")
        lines.append(f"[{section.name}]")
        for key, value in section.properties.items():
            lines.append(f"{key} = {value}")
    lines.append("")
    return "\n".join(lines)


def validate_layout(doc: LayoutDocument) -> list[str]:
    """Semantic validation. Returns a list of error strings (empty = ok)."""
    errors: list[str] = []

    if doc.doc_type != DOC_TYPE:
        errors.append(
            f"未知的文档类型: {doc.doc_type!r} (期望 {DOC_TYPE!r})"
        )
        return errors

    for section in doc.sections:
        where = f"节 [{section.name}]"
        if not SECTION_NAME_RE.match(section.name):
            errors.append(
                f"{where}: 非法节名 (仅允许小写字母、数字、'_'、'-' 与 ':' 分隔)"
            )
            continue

        parts = section.name.split(":")
        if parts[0] == "element":
            if len(parts) != 3:
                errors.append(
                    f"{where}: 元素节必须是 [element:<type>:<identifier>]"
                )
                continue
            _, element_type, identifier = parts
            if element_type not in ELEMENT_TYPES:
                errors.append(
                    f"{where}: 未知元素类型 {element_type!r} "
                    f"(已知: {', '.join(sorted(ELEMENT_TYPES))})"
                )
            if not IDENTIFIER_RE.match(identifier):
                errors.append(
                    f"{where}: 非法元素标识符 {identifier!r}"
                )

            for prop, value in section.properties.items():
                prop_where = f"{where} 属性 {prop}"
                if prop in INT_PROPERTIES:
                    if not re.fullmatch(r"-?\d+", value):
                        errors.append(f"{prop_where}: 需要整数 (得到 {value!r})")
                elif prop in FLOAT_PROPERTIES:
                    try:
                        number = float(value)
                    except ValueError:
                        errors.append(f"{prop_where}: 需要浮点数 (得到 {value!r})")
                    else:
                        if prop == "opacity" and not 0.0 <= number <= 1.0:
                            errors.append(
                                f"{prop_where}: opacity 必须在 0.0-1.0 之间"
                            )
                elif prop in ENUM_PROPERTIES:
                    allowed = ENUM_PROPERTIES[prop]
                    if value not in allowed:
                        errors.append(
                            f"{prop_where}: {prop} 必须是 "
                            f"{', '.join(sorted(allowed))} 之一 (得到 {value!r})"
                        )

    return errors


def default_filename(doc: LayoutDocument) -> str:
    """Derive a layout file name from the document."""
    for section in doc.sections:
        if section.name == "layout-meta":
            name = section.properties.get("name") or section.properties.get(
                "layout_name"
            )
            if name:
                slug = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")
                if slug:
                    return f"{slug}.txt"
    return "layout.txt"


def from_plain(plain: dict) -> LayoutDocument:
    """Rebuild a LayoutDocument from its plain (JSON) view."""
    sections = [
        Section(
            str(section.get("name", "")),
            dict(section.get("properties") or {}),
        )
        for section in plain.get("sections", [])
    ]
    return LayoutDocument(
        doc_type=str(plain.get("type", DOC_TYPE)),
        sections=sections,
    )
