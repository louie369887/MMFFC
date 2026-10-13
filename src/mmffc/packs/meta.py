"""资源包 / 数据包元数据（pack.mcmeta）与 pack_format 版本表。

pack.mcmeta 是资源包与数据包的身份标识::

    {"pack": {"pack_format": 15, "description": "..."}}

pack_format 与游戏版本一一对应（1.20–1.20.1 = 15）。1.20.2 起引入
可选 ``supported_formats``；25w31a（1.21.9+）起改用 ``min_format`` /
``max_format`` 且 ``pack_format`` 变为可选。MMFFC 以 1.20.1 为目标，
校验规则：``pack_format`` 存在时必须等于 15（否则提示不兼容），
缺失时按新版语义放行并给出 warning。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

TARGET_FORMAT = 15  # 1.20 – 1.20.1

# pack_format -> 版本区间（节选，覆盖 1.20.x 目标与常见回退）
PACK_FORMATS: dict[int, str] = {
    4: "1.13-1.14.4",
    5: "1.15-1.16.1",
    6: "1.16.2-1.16.5",
    7: "1.17-1.17.1",
    8: "1.18-1.18.1",
    9: "1.18.2",
    12: "1.19.3",
    13: "1.19.4",
    15: "1.20-1.20.1",
    18: "1.20.2",
    22: "1.20.3-1.20.4",
    26: "1.20.5-1.20.6",
    32: "1.21-1.21.1",
    41: "1.21.2-1.21.3",
    48: "1.21.4",
}

META_NAME = "pack.mcmeta"
PACK_PNG = "pack.png"


@dataclass
class PackMeta:
    """解析后的 pack.mcmeta（缺失字段以 None 表示）。"""

    pack_format: int | None
    description: str | None
    min_format: int | None = None
    max_format: int | None = None
    supported_formats: Any = None
    found: bool = False
    error: str | None = None

    @property
    def compatible_with_target(self) -> bool:
        """是否与 1.20.1（pack_format 15）兼容。"""
        if self.pack_format is None:
            return self.min_format is None or self.min_format <= TARGET_FORMAT
        if self.min_format is not None and self.min_format > TARGET_FORMAT:
            return False
        if self.max_format is not None and self.max_format < TARGET_FORMAT:
            return False
        return self.pack_format == TARGET_FORMAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "pack_format": self.pack_format,
            "pack_format_target": TARGET_FORMAT,
            "pack_format_versions": PACK_FORMATS.get(self.pack_format)
            if self.pack_format is not None
            else None,
            "description": self.description,
            "min_format": self.min_format,
            "max_format": self.max_format,
            "supported_formats": self.supported_formats,
            "compatible": self.compatible_with_target,
            "error": self.error,
        }


def _format_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, int) and not isinstance(first, bool):
            return first
    return None


def parse_pack_meta(text: str) -> PackMeta:
    """解析 pack.mcmeta 文本（JSON 容错）。"""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return PackMeta(None, None, error=f"pack.mcmeta 不是合法 JSON: {exc.msg}")
    if not isinstance(document, dict):
        return PackMeta(None, None, error="pack.mcmeta 根节点必须是对象")
    pack = document.get("pack")
    if not isinstance(pack, dict):
        return PackMeta(None, None, error="pack.mcmeta 缺少 pack 对象")

    description = pack.get("description")
    if description is not None and not isinstance(description, (str, list, dict)):
        description = None
    supported = pack.get("supported_formats")
    if supported is not None and not isinstance(supported, (int, list, dict)):
        supported = None
    return PackMeta(
        pack_format=_format_value(pack.get("pack_format")),
        description=description if isinstance(description, str) else None,
        min_format=_format_value(pack.get("min_format")),
        max_format=_format_value(pack.get("max_format")),
        supported_formats=supported,
        found=True,
    )


def read_pack_meta(root) -> PackMeta:
    """从目录或 zip 内的 pack.mcmeta 读取元数据。"""
    path = _join_meta(root)
    if path is None:
        return PackMeta(None, None)
    try:
        if hasattr(path, "read_text"):
            return parse_pack_meta(path.read_text(encoding="utf-8-sig"))
        return parse_pack_meta(path.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        return PackMeta(None, None, error=f"读取 pack.mcmeta 失败: {exc}")


def _join_meta(root):
    """支持 pathlib.Path 与 {文件名: 字节} 映射两种来源。"""
    if isinstance(root, dict):
        return root.get(META_NAME)
    path = root / META_NAME
    return path if path.is_file() else None


def describe_format(pack_format: int | None) -> str:
    """pack_format -> 人类可读版本区间。"""
    if pack_format is None:
        return "未知（未声明 pack_format）"
    return PACK_FORMATS.get(pack_format, f"未知区间 (format={pack_format})")
