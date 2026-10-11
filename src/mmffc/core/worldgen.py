"""世界生成规则（world rules）schema 校验与确定性生成（CLI.md 3.4）。

规则文件由 MCP 的 ``world_gen_rule_engine`` 产出，CLI ``world build``
消费。结构::

    {
      "name": "test_world",        # 可选，用于 level.dat LevelName
      "seed": 12345,               # 可选，默认 0
      "area": {"x": 0, "z": 0, "w": 16, "d": 16},   # layers 必需
      "layers": [                  # 长方体铺层，先应用（从低到高）
        {"from": -60, "to": -58, "block": "minecraft:stone"},
        {"from": -57, "to": -55, "block": "minecraft:dirt",
         "properties": {}}
      ],
      "ops": [ ... ],              # 形状引擎 op（voxelizer_brain 同语义）
      "blocks": [ ... ]            # 显式体素，最后应用
    }

执行顺序固定为 **layers -> ops -> blocks**（后者覆盖前者），
结果再按 y,z,x 排序输出，保证字节级可复现。

严格性：未知键、缺键、类型错误一律 SchemaError（exit 3）。
"""

from __future__ import annotations

from typing import Any, Callable

from mmffc.core.errors import SchemaError
from mmffc.core.voxels import (
    AIR,
    MAX_VOLUME,
    Voxel,
    _BLOCK_RE,
    _parse_properties,
    apply_ops,
    voxel_from_record,
)

MAX_COORD = 30_000_000
WORLD_MIN_Y = -64
WORLD_MAX_Y = 319

RULE_KEYS = frozenset({"name", "seed", "area", "layers", "ops", "blocks"})
AREA_KEYS = frozenset({"x", "z", "w", "d"})
LAYER_KEYS = frozenset({"from", "to", "block", "properties"})

DEFAULT_AREA = {"x": 0, "z": 0, "w": 16, "d": 16}
DEFAULT_LAYERS = [
    {"from": 0, "to": 0, "block": "minecraft:stone"},
]


def _require_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{where} 必须是整数")
    return value


def _normalize_block(block: Any, where: str) -> str:
    if not isinstance(block, str) or not block:
        raise SchemaError(f"{where} block 必须是非空字符串")
    normalized = block if ":" in block else f"minecraft:{block}"
    if not _BLOCK_RE.match(normalized):
        raise SchemaError(f"{where} 非法方块 ID: {block!r}")
    return normalized


def validate_rules(doc: Any) -> dict[str, Any]:
    """校验并归一化规则文档（返回新的 dict）。"""
    if not isinstance(doc, dict):
        raise SchemaError("规则文件必须是 JSON 对象")

    unknown = set(doc) - RULE_KEYS
    if unknown:
        raise SchemaError(
            f"规则包含未知字段: {', '.join(sorted(unknown))}"
        )

    rules: dict[str, Any] = {}
    if "name" in doc:
        name = doc["name"]
        if not isinstance(name, str) or not name.strip():
            raise SchemaError("规则 name 必须是非空字符串")
        rules["name"] = name

    if "seed" in doc:
        seed = _require_int(doc["seed"], "规则 seed")
        rules["seed"] = seed

    layers = doc.get("layers", [])
    if not isinstance(layers, list):
        raise SchemaError("规则 layers 必须是数组")

    area: dict[str, int] | None = None
    if "area" in doc:
        raw_area = doc["area"]
        if not isinstance(raw_area, dict):
            raise SchemaError("规则 area 必须是对象")
        unknown_area = set(raw_area) - AREA_KEYS
        if unknown_area:
            raise SchemaError(
                f"area 包含未知字段: {', '.join(sorted(unknown_area))}"
            )
        for key in sorted(AREA_KEYS):
            if key not in raw_area:
                raise SchemaError(f"area 缺少字段 {key}")
        area = {
            key: _require_int(raw_area[key], f"area.{key}")
            for key in sorted(AREA_KEYS)
        }
        if area["w"] <= 0 or area["d"] <= 0:
            raise SchemaError("area.w / area.d 必须为正")
        if abs(area["x"]) > MAX_COORD or abs(area["z"]) > MAX_COORD:
            raise SchemaError(f"area.x / area.z 超出 ±{MAX_COORD}")
        if area["w"] > MAX_VOLUME or area["d"] > MAX_VOLUME:
            raise SchemaError("area.w / area.d 超出上限")
        rules["area"] = area
    elif layers:
        raise SchemaError("使用 layers 时必须提供 area")

    normalized_layers: list[dict[str, Any]] = []
    for index, layer in enumerate(layers):
        where = f"layers[{index}]"
        if not isinstance(layer, dict):
            raise SchemaError(f"{where} 必须是对象")
        unknown_layer = set(layer) - LAYER_KEYS
        if unknown_layer:
            raise SchemaError(
                f"{where} 包含未知字段: {', '.join(sorted(unknown_layer))}"
            )
        for key in ("from", "to", "block"):
            if key not in layer:
                raise SchemaError(f"{where} 缺少字段 {key}")
        start = _require_int(layer["from"], f"{where}.from")
        end = _require_int(layer["to"], f"{where}.to")
        if start > end:
            raise SchemaError(f"{where}: from 必须 <= to")
        if not WORLD_MIN_Y <= start or end > WORLD_MAX_Y:
            raise SchemaError(
                f"{where}: from/to 必须落在 [{WORLD_MIN_Y},{WORLD_MAX_Y}]"
            )
        block = _normalize_block(layer["block"], where)
        properties = _parse_properties(layer.get("properties"), where)
        if area is not None:
            span = area["w"] * area["d"] * (end - start + 1)
            if span > MAX_VOLUME:
                raise SchemaError(
                    f"{where}: 铺层体积 {span} 超出上限 {MAX_VOLUME}"
                )
        entry: dict[str, Any] = {"from": start, "to": end, "block": block}
        if properties:
            entry["properties"] = dict(properties)
        normalized_layers.append(entry)
    if normalized_layers:
        rules["layers"] = normalized_layers

    ops = doc.get("ops", [])
    if not isinstance(ops, list):
        raise SchemaError("规则 ops 必须是数组")
    if ops:
        rules["ops"] = ops

    blocks = doc.get("blocks", [])
    if not isinstance(blocks, list):
        raise SchemaError("规则 blocks 必须是数组")
    if blocks:
        for index, record in enumerate(blocks):
            voxel_from_record(record, f"规则 blocks[{index}]")
        rules["blocks"] = blocks

    if not (normalized_layers or ops or blocks):
        raise SchemaError(
            "规则未定义任何内容（layers / ops / blocks 至少一项）"
        )
    return rules


ProgressFn = Callable[[int, int, str], None]  # (done, total, phase)


def generate_from_rules(
    rules: dict[str, Any],
    progress: ProgressFn | None = None,
) -> list[Voxel]:
    """按 layers -> ops -> blocks 顺序生成体素。

    ``progress(done, total, phase)`` 为可选进度回调（单调递增），
    供 MCP 长时操作上报（SDK 无 Tasks 扩展时的等价机制）。
    """

    def _report(done: int, total: int, phase: str) -> None:
        if progress is not None:
            progress(done, total, phase)

    cells: dict[tuple[int, int, int], tuple[str, tuple[tuple[str, str], ...]]] = {}

    area = rules.get("area")
    layers = rules.get("layers", [])
    rows = sum(layer["to"] - layer["from"] + 1 for layer in layers)
    done_rows = 0
    for index, layer in enumerate(layers):
        if area is None:
            raise SchemaError(f"layers[{index}] 存在但缺少 area")
        block = layer["block"]
        props = _parse_properties(layer.get("properties"), f"layers[{index}]")
        for y in range(layer["from"], layer["to"] + 1):
            for z in range(area["z"], area["z"] + area["d"]):
                for x in range(area["x"], area["x"] + area["w"]):
                    cells[(x, y, z)] = (block, props)
            done_rows += 1
            _report(done_rows, rows, f"layers[{index}] y={y}")
    _report(rows, rows, "ops")

    apply_ops(cells, rules.get("ops", []))
    _report(rows, rows, "blocks")

    for index, record in enumerate(rules.get("blocks", [])):
        voxel = voxel_from_record(record, f"规则 blocks[{index}]")
        cells[voxel.coord] = (voxel.block, voxel.properties)

    if not cells:
        raise SchemaError("规则未生成任何方块")
    if len(cells) > MAX_VOLUME:
        raise SchemaError(
            f"规则生成了 {len(cells)} 个方块，超过上限 {MAX_VOLUME}"
        )

    non_air = [
        Voxel(coord[0], coord[1], coord[2], block, props)
        for coord, (block, props) in cells.items()
        if not (block == AIR and not props)
    ]
    if not non_air:
        raise SchemaError("规则只生成了空气，没有任何实体方块")

    non_air.sort(key=lambda v: (v.y, v.z, v.x, v.block, v.properties))
    return non_air


def build_default_rules(description: str = "", context: Any = None) -> dict[str, Any]:
    """MCP ``world_gen_rule_engine`` 的默认规则构造。"""
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise SchemaError("context 必须是对象")
    rules: dict[str, Any] = dict(context)

    rules.setdefault("name", (description or "mmffc_world").strip() or "mmffc_world")
    # 仅在完全没提供内容时补默认平台（有任一内容就不插手）
    if not any(rules.get(key) for key in ("area", "layers", "ops", "blocks")):
        rules["area"] = dict(DEFAULT_AREA)
        rules["layers"] = [dict(DEFAULT_LAYERS[0])]
    return validate_rules(rules)
