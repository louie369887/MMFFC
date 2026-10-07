"""NBT (Named Binary Tag) 二进制读写（Java 版大端序，gzip 感知）。

供 structure ``.nbt`` 与 ``.litematic`` 文件使用（CLI.md 3.3）。

Python 类型自动推断规则（写出时）：

* ``bool`` -> TAG_Byte、``int`` -> TAG_Int（超出 int32 自动 TAG_Long）、
  ``float`` -> TAG_Double、``str`` -> TAG_String
* ``bytes`` -> TAG_Byte_Array、``dict`` -> TAG_Compound、
  ``list`` -> TAG_List（元素类型依首个元素推断；**空列表必须用
  :class:`ListOf` 显式指定元素类型**）
* 显式包装器（Byte/Short/Int/Long/Float/Double/ByteArray/IntArray/
  LongArray/ListOf）可覆盖推断结果，保证字节级精确可控

解析结果保留包装器子类（Int/Long 等是 int 子类），因此与普通
Python 值比较相等，同时写出时可还原原始标签类型（roundtrip 保真）。
"""

from __future__ import annotations

import gzip
import struct
import zlib
from typing import Any

from mmffc.core.errors import SchemaError

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

_INT32_MIN = -(1 << 31)
_INT32_MAX = (1 << 31) - 1
_MAX_STRING = 0xFFFF

GZIP_MAGIC = b"\x1f\x8b"


class Byte(int):
    tag_id = TAG_BYTE


class Short(int):
    tag_id = TAG_SHORT


class Int(int):
    tag_id = TAG_INT


class Long(int):
    tag_id = TAG_LONG


class Float(float):
    tag_id = TAG_FLOAT


class Double(float):
    tag_id = TAG_DOUBLE


class ByteArray(bytes):
    tag_id = TAG_BYTE_ARRAY


class IntArray(list):
    tag_id = TAG_INT_ARRAY


class LongArray(list):
    tag_id = TAG_LONG_ARRAY


class ListOf(list):
    """带显式元素类型的 TAG_List（空列表的唯一合法写法）。"""

    tag_id = TAG_LIST

    def __init__(self, elem_type: int, items: Any = ()) -> None:
        super().__init__(items)
        tag = getattr(elem_type, "tag_id", elem_type)
        if not isinstance(tag, int) or isinstance(tag, bool) or not 0 <= tag <= TAG_LONG_ARRAY:
            raise SchemaError(f"ListOf 元素类型非法: {elem_type!r}")
        self.elem_type = tag


def tag_id_of(value: Any) -> int:
    """推断值的 NBT 标签类型（列表恒为 TAG_List，元素类型另判）。"""
    if isinstance(
        value,
        (Byte, Short, Int, Long, Float, Double, ByteArray, IntArray, LongArray, ListOf),
    ):
        return value.tag_id
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT if _INT32_MIN <= value <= _INT32_MAX else TAG_LONG
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, (bytes, bytearray, memoryview)):
        return TAG_BYTE_ARRAY
    if isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, (list, tuple)):
        if not value:
            raise SchemaError(
                "NBT 空列表缺少元素类型，请使用 ListOf(elem_type, [])"
            )
        return TAG_LIST
    raise SchemaError(f"NBT 不支持的 Python 类型: {type(value).__name__}")


# ----------------------------------------------------------------------
# 写出
# ----------------------------------------------------------------------
def _write_string(out: bytearray, raw: bytes, what: str) -> None:
    if len(raw) > _MAX_STRING:
        raise SchemaError(f"{what}超过 {_MAX_STRING} 字节")
    out += struct.pack(">H", len(raw))
    out += raw


def _write_payload(out: bytearray, tag: int, value: Any) -> None:
    try:
        if tag == TAG_BYTE:
            out += struct.pack(">b", int(value))
        elif tag == TAG_SHORT:
            out += struct.pack(">h", int(value))
        elif tag == TAG_INT:
            out += struct.pack(">i", int(value))
        elif tag == TAG_LONG:
            out += struct.pack(">q", int(value))
        elif tag == TAG_FLOAT:
            out += struct.pack(">f", float(value))
        elif tag == TAG_DOUBLE:
            out += struct.pack(">d", float(value))
        elif tag == TAG_BYTE_ARRAY:
            raw = bytes(value)
            out += struct.pack(">i", len(raw))
            out += raw
        elif tag == TAG_STRING:
            _write_string(out, str(value).encode("utf-8"), "NBT 字符串")
        elif tag == TAG_LIST:
            if isinstance(value, ListOf):
                elem = value.elem_type
                items: list = list(value)
            elif isinstance(value, (list, tuple)):
                if not value:
                    raise SchemaError(
                        "NBT 空列表缺少元素类型，请使用 ListOf(elem_type, [])"
                    )
                elem = tag_id_of(value[0])
                items = list(value)
            else:
                raise SchemaError("TAG_List 的值必须是 list")
            out += struct.pack(">bi", elem, len(items))
            for item in items:
                _write_payload(out, elem, item)
        elif tag == TAG_COMPOUND:
            if not isinstance(value, dict):
                raise SchemaError("TAG_Compound 的值必须是 dict")
            for key, item in value.items():
                child = tag_id_of(item)
                out += struct.pack(">b", child)
                _write_string(out, str(key).encode("utf-8"), "NBT 键名")
                _write_payload(out, child, item)
            out += b"\x00"
        elif tag == TAG_INT_ARRAY:
            out += struct.pack(">i", len(value))
            if value:
                out += struct.pack(f">{len(value)}i", *[int(v) for v in value])
        elif tag == TAG_LONG_ARRAY:
            out += struct.pack(">i", len(value))
            if value:
                out += struct.pack(f">{len(value)}q", *[int(v) for v in value])
        else:
            raise SchemaError(f"无法写出的 NBT 标签类型: {tag}")
    except (struct.error, TypeError, ValueError) as exc:
        raise SchemaError(f"NBT 写入失败 (tag={tag}): {exc}") from exc


def write_nbt(name: str, value: Any) -> bytes:
    """序列化为裸 NBT 字节（根恒为 compound）。"""
    if not isinstance(value, dict):
        raise SchemaError("NBT 根值必须是 dict (compound)")
    try:
        out = bytearray()
        out += struct.pack(">b", TAG_COMPOUND)
        _write_string(out, str(name).encode("utf-8"), "NBT 根名称")
        _write_payload(out, TAG_COMPOUND, value)
        return bytes(out)
    except RecursionError as exc:
        raise SchemaError("NBT 结构嵌套过深") from exc


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
class _Cursor:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0:
            raise SchemaError(f"NBT 负长度 {n} (偏移 {self.pos})")
        end = self.pos + n
        if end > len(self.data):
            raise SchemaError(
                f"NBT 数据截断 (偏移 {self.pos}, 需要 {n} 字节, 共 {len(self.data)} 字节)"
            )
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        values = struct.unpack(fmt, self.take(size))
        return values[0]


def _read_string(cur: _Cursor) -> str:
    length = cur.unpack(">H")
    raw = cur.take(length)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError(f"NBT 字符串不是合法 UTF-8 (偏移 {cur.pos})") from exc


def _read_payload(cur: _Cursor, tag: int) -> Any:
    if tag == TAG_BYTE:
        return Byte(cur.unpack(">b"))
    if tag == TAG_SHORT:
        return Short(cur.unpack(">h"))
    if tag == TAG_INT:
        return Int(cur.unpack(">i"))
    if tag == TAG_LONG:
        return Long(cur.unpack(">q"))
    if tag == TAG_FLOAT:
        return Float(cur.unpack(">f"))
    if tag == TAG_DOUBLE:
        return Double(cur.unpack(">d"))
    if tag == TAG_BYTE_ARRAY:
        return ByteArray(cur.take(cur.unpack(">i")))
    if tag == TAG_STRING:
        return _read_string(cur)
    if tag == TAG_LIST:
        elem = cur.unpack(">b")
        count = cur.unpack(">i")
        if count < 0:
            raise SchemaError(f"NBT 列表负长度 {count} (偏移 {cur.pos})")
        if count > len(cur.data) - cur.pos:
            raise SchemaError(
                f"NBT 列表长度异常: {count} 项 (偏移 {cur.pos}, 剩余 {len(cur.data) - cur.pos} 字节)"
            )
        return ListOf(elem, [_read_payload(cur, elem) for _ in range(count)])
    if tag == TAG_COMPOUND:
        result: dict[str, Any] = {}
        while True:
            child_tag = cur.unpack(">b")
            if child_tag == TAG_END:
                break
            if child_tag < TAG_BYTE or child_tag > TAG_LONG_ARRAY:
                raise SchemaError(f"未知的 NBT 标签类型: {child_tag} (偏移 {cur.pos})")
            key = _read_string(cur)
            result[key] = _read_payload(cur, child_tag)
        return result
    if tag == TAG_INT_ARRAY:
        count = cur.unpack(">i")
        if count < 0:
            raise SchemaError(f"NBT IntArray 负长度 {count}")
        if count * 4 > len(cur.data) - cur.pos:
            raise SchemaError(f"NBT IntArray 长度异常: {count} (偏移 {cur.pos})")
        return IntArray(struct.unpack(f">{count}i", cur.take(count * 4)))
    if tag == TAG_LONG_ARRAY:
        count = cur.unpack(">i")
        if count < 0:
            raise SchemaError(f"NBT LongArray 负长度 {count}")
        if count * 8 > len(cur.data) - cur.pos:
            raise SchemaError(f"NBT LongArray 长度异常: {count} (偏移 {cur.pos})")
        return LongArray(struct.unpack(f">{count}q", cur.take(count * 8)))
    raise SchemaError(f"未知的 NBT 标签类型: {tag} (偏移 {cur.pos})")


def parse_nbt(data: bytes) -> tuple[str, Any]:
    """解析裸 NBT 或 gzip NBT（自动探测魔数）。返回 (根名称, 根值)。"""
    raw = maybe_decompress(bytes(data))
    if not raw:
        raise SchemaError("NBT 数据为空")
    try:
        cur = _Cursor(raw)
        root_tag = cur.unpack(">b")
        if root_tag < TAG_BYTE or root_tag > TAG_LONG_ARRAY:
            raise SchemaError(f"未知的 NBT 根标签类型: {root_tag}")
        name = _read_string(cur)
        value = _read_payload(cur, root_tag)
        return name, value
    except RecursionError as exc:
        raise SchemaError("NBT 结构嵌套过深") from exc


# ----------------------------------------------------------------------
# gzip
# ----------------------------------------------------------------------
def gzip_compress(data: bytes) -> bytes:
    """确定性 gzip（mtime=0，输出字节可复现）。"""
    return gzip.compress(data, mtime=0)


def maybe_decompress(data: bytes) -> bytes:
    """gzip 魔数探测；非 gzip 原样返回。"""
    if data[:2] == GZIP_MAGIC:
        try:
            return gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as exc:
            raise SchemaError(f"gzip 解压失败: {exc}") from exc
    return data
