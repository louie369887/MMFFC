"""体素 NDJSON 模型、网格构建与形状引擎（CLI.md 3.3）。

NDJSON 行格式::

    {"x": 0, "y": 0, "z": 0, "block": "minecraft:oak_planks", "properties": {"facing": "north"}}

* ``block`` 缺少命名空间时自动补 ``minecraft:``
* 重复坐标、未知字段、非整数坐标均为错误（SchemaError，exit 3）

Grid 不变量：``palette[0]`` 恒为 ``minecraft:air``；``cells`` 为稠密
调色板索引（x 最快：``index = (y * sizeZ + z) * sizeX + x``），
未绘制格子为空气（0）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from mmffc.core.errors import SchemaError

MAX_AXIS = 512
MAX_VOLUME = 1_048_576

AIR = "minecraft:air"

_BLOCK_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_ALLOWED_FIELDS = frozenset({"x", "y", "z", "block", "properties"})


@dataclass(frozen=True)
class Voxel:
    x: int
    y: int
    z: int
    block: str
    properties: tuple[tuple[str, str], ...] = ()

    @property
    def coord(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)

    @property
    def is_air(self) -> bool:
        return self.block == AIR and not self.properties


# ----------------------------------------------------------------------
# NDJSON 解析 / 序列化
# ----------------------------------------------------------------------
def _parse_properties(raw: Any, where: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise SchemaError(f"{where} properties 必须是对象")
    items: list[tuple[str, str]] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise SchemaError(f"{where} 非法属性名: {key!r}")
        if isinstance(value, bool):
            normalized = "true" if value else "false"
        elif isinstance(value, (int, float)):
            normalized = str(value)
        elif isinstance(value, str):
            normalized = value
        else:
            raise SchemaError(f"{where} 属性 {key} 的值必须是标量")
        items.append((key, normalized))
    return tuple(sorted(items))


def voxel_from_record(record: Any, where: str) -> Voxel:
    """从单个 JSON 对象构造 Voxel（供 NDJSON 行与 MCP blocks 复用）。"""
    if not isinstance(record, dict):
        raise SchemaError(f"{where} 必须是 JSON 对象")
    unknown = set(record) - _ALLOWED_FIELDS
    if unknown:
        raise SchemaError(f"{where} 未知字段: {', '.join(sorted(unknown))}")
    coords: list[int] = []
    for axis in ("x", "y", "z"):
        if axis not in record:
            raise SchemaError(f"{where} 缺少字段 {axis}")
        value = record[axis]
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{where} {axis} 必须是整数")
        coords.append(value)
    block = record.get("block")
    if not isinstance(block, str) or not block:
        raise SchemaError(f"{where} 缺少 block")
    if ":" not in block:
        block = f"minecraft:{block}"
    if not _BLOCK_RE.match(block):
        raise SchemaError(f"{where} 非法方块 ID: {block!r}")
    properties = _parse_properties(record.get("properties"), where)
    return Voxel(coords[0], coords[1], coords[2], block, properties)


def parse_ndjson(text: str) -> list[Voxel]:
    """解析体素 NDJSON；行号错误，重复坐标报错。"""
    if text.startswith("\ufeff"):
        text = text[1:]  # 容忍 UTF-8 BOM
    voxels: list[Voxel] = []
    seen: dict[tuple[int, int, int], int] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        where = f"第 {lineno} 行"
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{where} JSON 解析失败: {exc.msg}") from None
        voxel = voxel_from_record(record, where)
        if voxel.coord in seen:
            raise SchemaError(
                f"{where} 坐标 {voxel.coord} 与第 {seen[voxel.coord]} 行重复"
            )
        seen[voxel.coord] = lineno
        voxels.append(voxel)
    if not voxels:
        raise SchemaError("体素数据为空（无有效 NDJSON 行）")
    return voxels


def dumps_ndjson(voxels: Iterable[Voxel]) -> str:
    """确定性序列化（按 y,z,x 排序）。"""
    ordered = sorted(voxels, key=lambda v: (v.y, v.z, v.x, v.block, v.properties))
    lines: list[str] = []
    for voxel in ordered:
        record: dict[str, Any] = {
            "x": voxel.x,
            "y": voxel.y,
            "z": voxel.z,
            "block": voxel.block,
        }
        if voxel.properties:
            record["properties"] = dict(voxel.properties)
        lines.append(json.dumps(record, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)


# ----------------------------------------------------------------------
# Grid
# ----------------------------------------------------------------------
@dataclass
class Grid:
    size: tuple[int, int, int]
    palette: list[tuple[str, tuple[tuple[str, str], ...]]]
    cells: list[int]
    origin: tuple[int, int, int] = (0, 0, 0)

    @property
    def volume(self) -> int:
        sx, sy, sz = self.size
        return sx * sy * sz

    @property
    def block_count(self) -> int:
        """非空气方块数。"""
        return sum(1 for index in self.cells if index != 0)

    def block_at(self, x: int, y: int, z: int) -> tuple[str, tuple[tuple[str, str], ...]]:
        """相对坐标取方块（空气返回 (AIR, ())）。"""
        sx, sy, sz = self.size
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            raise SchemaError(f"坐标越界: {(x, y, z)} size={self.size}")
        return self.palette[self.cells[(y * sz + z) * sx + x]]


def build_grid(
    voxels: Iterable[Voxel],
    origin: tuple[int, int, int] | None = None,
    size: tuple[int, int, int] | None = None,
) -> Grid:
    """体素集合 -> 稠密网格（palette[0] 恒为空气）。

    * ``origin=None``：贴合体素最小角
    * ``origin`` 显式给定时，小于最小角的方向补空气，大于则报错
    * ``size`` 显式给定（解析外部文件时保持声明尺寸），必须容得下全部体素
    """
    items = list(voxels)
    if not items:
        raise SchemaError("体素数据为空")

    min_x = min(v.x for v in items)
    min_y = min(v.y for v in items)
    min_z = min(v.z for v in items)
    max_x = max(v.x for v in items)
    max_y = max(v.y for v in items)
    max_z = max(v.z for v in items)

    if origin is None:
        origin = (min_x, min_y, min_z)
    else:
        origin = (int(origin[0]), int(origin[1]), int(origin[2]))
        if origin[0] > min_x or origin[1] > min_y or origin[2] > min_z:
            raise SchemaError(
                f"origin {origin} 超出体素最小角 {(min_x, min_y, min_z)}"
                "（origin 必须不大于最小角，否则体素相对坐标为负）"
            )

    computed = (max_x - origin[0] + 1, max_y - origin[1] + 1, max_z - origin[2] + 1)
    if size is None:
        size = computed
    else:
        size = (int(size[0]), int(size[1]), int(size[2]))
        if any(s <= 0 for s in size):
            raise SchemaError(f"非法尺寸: {size}")
        if size[0] < computed[0] or size[1] < computed[1] or size[2] < computed[2]:
            raise SchemaError(
                f"声明尺寸 {size} 容不下体素范围 {computed} (origin={origin})"
            )

    sx, sy, sz = size
    if max(sx, sy, sz) > MAX_AXIS:
        raise SchemaError(f"轴长 {size} 超出上限 {MAX_AXIS}")
    volume = sx * sy * sz
    if volume > MAX_VOLUME:
        raise SchemaError(
            f"体积 {volume} 超出上限 {MAX_VOLUME}（建议拆分结构或提高 origin 精度）"
        )

    non_air_keys = sorted(
        {(v.block, v.properties) for v in items if not v.is_air}
    )
    palette: list[tuple[str, tuple[tuple[str, str], ...]]] = [(AIR, ())]
    lookup: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {(AIR, ()): 0}
    for key in non_air_keys:
        lookup[key] = len(palette)
        palette.append(key)

    cells = [0] * volume
    for voxel in items:
        rel_x = voxel.x - origin[0]
        rel_y = voxel.y - origin[1]
        rel_z = voxel.z - origin[2]
        cells[(rel_y * sz + rel_z) * sx + rel_x] = lookup[(voxel.block, voxel.properties)]

    return Grid(size=size, palette=palette, cells=cells, origin=origin)


def grid_to_voxels(grid: Grid, *, include_air: bool = False) -> list[Voxel]:
    """网格展开为体素列表（默认省略空气，语义等价：未绘制即空气）。"""
    sx, sy, sz = grid.size
    ox, oy, oz = grid.origin
    voxels: list[Voxel] = []
    index = 0
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                block, props = grid.palette[grid.cells[index]]
                index += 1
                if block == AIR and not props and not include_air:
                    continue
                voxels.append(Voxel(ox + x, oy + y, oz + z, block, props))
    return voxels


# ----------------------------------------------------------------------
# 形状引擎（voxelizer_brain）
# ----------------------------------------------------------------------
def _int_param(op: dict, key: str, shape: str, default: int | None = None) -> int:
    if key not in op:
        if default is None:
            raise SchemaError(f"shape {shape!r} 缺少参数 {key}")
        return default
    value = op[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"shape {shape!r} 参数 {key} 必须是整数")
    return value


def _guard_bounds(shape: str, xs: range, ys: range, zs: range) -> None:
    count = len(xs) * len(ys) * len(zs)
    if count <= 0:
        raise SchemaError(f"shape {shape!r} 尺寸为空")
    if count > MAX_VOLUME:
        raise SchemaError(f"shape {shape!r} 体积 {count} 超出上限 {MAX_VOLUME}")


def _paint_box(
    cells: dict, block: str, props: tuple, x: int, y: int, z: int,
    w: int, h: int, d: int, thickness: int | None,
) -> None:
    for ry in range(h):
        for rz in range(d):
            for rx in range(w):
                if thickness is not None and (
                    thickness <= rx < w - thickness
                    and thickness <= ry < h - thickness
                    and thickness <= rz < d - thickness
                ):
                    continue
                cells[(x + rx, y + ry, z + rz)] = (block, props)


def _paint_sphere(
    cells: dict, block: str, props: tuple,
    cx: int, cy: int, cz: int, radius: int, thickness: int | None,
) -> None:
    outer = radius * radius
    if thickness is None or radius - thickness <= 0:
        inner = -1  # 实心
    else:
        inner = (radius - thickness) ** 2
    r = radius
    for dy in range(-r, r + 1):
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                dist = dx * dx + dy * dy + dz * dz
                if dist <= outer and dist > inner:
                    cells[(cx + dx, cy + dy, cz + dz)] = (block, props)


def _paint_cylinder(
    cells: dict, block: str, props: tuple,
    cx: int, cy: int, cz: int, radius: int, length: int, axis: str,
) -> None:
    outer = radius * radius
    for offset in range(length):
        for da in range(-radius, radius + 1):
            for db in range(-radius, radius + 1):
                if da * da + db * db > outer:
                    continue
                if axis == "y":
                    cells[(cx + da, cy + offset, cz + db)] = (block, props)
                elif axis == "x":
                    cells[(cx + offset, cy + da, cz + db)] = (block, props)
                else:
                    cells[(cx + da, cy + db, cz + offset)] = (block, props)


def _line_cells(
    start: tuple[int, int, int], end: tuple[int, int, int]
) -> list[tuple[int, int, int]]:
    x0, y0, z0 = start
    x1, y1, z1 = end
    dx, dy, dz = abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)
    sx = -1 if x1 < x0 else 1
    sy = -1 if y1 < y0 else 1
    sz = -1 if z1 < z0 else 1
    cells: list[tuple[int, int, int]] = []
    x, y, z = x0, y0, z0
    if dx >= dy and dx >= dz:
        err_y = err_z = dx // 2
        cells.append((x, y, z))
        while x != x1:
            x += sx
            err_y += dy
            err_z += dz
            if err_y >= dx:
                err_y -= dx
                y += sy
            if err_z >= dx:
                err_z -= dz
                z += sz
            cells.append((x, y, z))
    elif dy >= dx and dy >= dz:
        err_x = err_z = dy // 2
        cells.append((x, y, z))
        while y != y1:
            y += sy
            err_x += dx
            err_z += dz
            if err_x >= dy:
                err_x -= dy
                x += sx
            if err_z >= dy:
                err_z -= dz
                z += sz
            cells.append((x, y, z))
    else:
        err_x = err_y = dz // 2
        cells.append((x, y, z))
        while z != z1:
            z += sz
            err_x += dx
            err_y += dy
            if err_x >= dz:
                err_x -= dz
                x += sx
            if err_y >= dz:
                err_y -= dy
                y += sy
            cells.append((x, y, z))
    return cells


def _apply_op(cells: dict, op: Any) -> None:
    if not isinstance(op, dict):
        raise SchemaError("context.ops 的每一项必须是对象")
    shape = op.get("shape")
    if not isinstance(shape, str):
        raise SchemaError("op 缺少 shape 字段")
    block = op.get("block", "stone")
    if not isinstance(block, str) or not block:
        raise SchemaError(f"shape {shape!r} block 必须是字符串")
    if ":" not in block:
        block = f"minecraft:{block}"
    if not _BLOCK_RE.match(block):
        raise SchemaError(f"shape {shape!r} 非法方块 ID: {block!r}")
    props = _parse_properties(op.get("properties"), f"shape {shape!r}")

    if shape in ("box", "hollow_box"):
        x = _int_param(op, "x", shape)
        y = _int_param(op, "y", shape)
        z = _int_param(op, "z", shape)
        w = _int_param(op, "w", shape)
        h = _int_param(op, "h", shape)
        d = _int_param(op, "d", shape)
        if w <= 0 or h <= 0 or d <= 0:
            raise SchemaError(f"shape {shape!r} w/h/d 必须为正")
        _guard_bounds(shape, range(w), range(h), range(d))
        thickness = None
        if shape == "hollow_box":
            thickness = _int_param(op, "thickness", shape, default=1)
            if thickness < 1 or 2 * thickness > min(w, h, d):
                raise SchemaError(f"shape {shape!r} thickness 非法: {thickness}")
        _paint_box(cells, block, props, x, y, z, w, h, d, thickness)
    elif shape in ("sphere", "hollow_sphere"):
        radius = _int_param(op, "r", shape)
        if radius < 1:
            raise SchemaError(f"shape {shape!r} r 必须 >= 1")
        _guard_bounds(shape, range(2 * radius + 1), range(2 * radius + 1), range(2 * radius + 1))
        thickness = None
        if shape == "hollow_sphere":
            thickness = _int_param(op, "thickness", shape, default=1)
            if thickness < 1:
                raise SchemaError(f"shape {shape!r} thickness 必须 >= 1")
        _paint_sphere(
            cells, block, props,
            _int_param(op, "cx", shape),
            _int_param(op, "cy", shape),
            _int_param(op, "cz", shape),
            radius, thickness,
        )
    elif shape == "cylinder":
        radius = _int_param(op, "r", shape)
        length = _int_param(op, "h", shape)
        if radius < 1 or length < 1:
            raise SchemaError("shape 'cylinder' r/h 必须 >= 1")
        axis = op.get("axis", "y")
        if axis not in ("x", "y", "z"):
            raise SchemaError(f"shape 'cylinder' axis 非法: {axis!r}")
        radial = range(2 * radius + 1)
        if axis == "x":
            xs, ys, zs = range(length), radial, radial
        elif axis == "y":
            xs, ys, zs = radial, range(length), radial
        else:
            xs, ys, zs = radial, radial, range(length)
        _guard_bounds(shape, xs, ys, zs)
        _paint_cylinder(
            cells, block, props,
            _int_param(op, "cx", shape),
            _int_param(op, "cy", shape),
            _int_param(op, "cz", shape),
            radius, length, axis,
        )
    elif shape == "line":
        def _point(key: str) -> tuple[int, int, int]:
            point = op.get(key)
            if (
                not isinstance(point, (list, tuple))
                or len(point) != 3
                or any(isinstance(v, bool) or not isinstance(v, int) for v in point)
            ):
                raise SchemaError(f"shape 'line' {key} 必须是 [x, y, z] 整数数组")
            return (point[0], point[1], point[2])

        if "from" not in op or "to" not in op:
            raise SchemaError("shape 'line' 缺少 from/to")
        start = _point("from")
        end = _point("to")
        span = max(abs(end[i] - start[i]) for i in range(3)) + 1
        _guard_bounds(shape, range(span), range(span), range(span))
        for cell in _line_cells(start, end):
            cells[cell] = (block, props)
    else:
        raise SchemaError(
            f"未知 shape: {shape!r} (可选: box, hollow_box, sphere, "
            "hollow_sphere, cylinder, line)"
        )


def generate(description: str, context: dict[str, Any] | None = None) -> list[Voxel]:
    """voxelizer_brain 的确定性生成器。

    ``context.ops`` 按顺序绘制（后者覆盖前者），``context.blocks``
    作为最顶层显式体素最后应用。两者皆空时输出 5x5 石头平台占位。
    """
    context = context or {}
    if not isinstance(context, dict):
        raise SchemaError("context 必须是对象")

    cells: dict[tuple[int, int, int], tuple[str, tuple[tuple[str, str], ...]]] = {}

    ops = context.get("ops", [])
    if not isinstance(ops, list):
        raise SchemaError("context.ops 必须是数组")
    for op in ops:
        _apply_op(cells, op)

    blocks = context.get("blocks", [])
    if not isinstance(blocks, list):
        raise SchemaError("context.blocks 必须是数组")
    for index, record in enumerate(blocks):
        voxel = voxel_from_record(record, f"blocks[{index}]")
        cells[voxel.coord] = (voxel.block, voxel.properties)

    if not cells:
        if not isinstance(description, str):
            raise SchemaError("description 必须是字符串")
        for x in range(5):
            for z in range(5):
                cells[(x, 0, z)] = ("minecraft:stone", ())

    if len(cells) > MAX_VOLUME:
        raise SchemaError(f"生成体积 {len(cells)} 超出上限 {MAX_VOLUME}")

    return [
        Voxel(coord[0], coord[1], coord[2], block, props)
        for coord, (block, props) in sorted(
            cells.items(), key=lambda item: (item[0][1], item[0][2], item[0][0])
        )
    ]
