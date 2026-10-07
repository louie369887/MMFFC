"""file_io_engine — 安全文件读写引擎 (PRD 3.1.2)。

所有写入操作经过两阶段校验：

* 第一阶段：JSON Schema 结构校验（jsonschema 库）
* 第二阶段：语义校验（格式模块提供，如方块 ID、坐标范围）

写入采用原子写入模式：先写临时文件，校验通过后
``os.replace()`` 替换原文件；每次写入前自动生成
``.bak`` 备份到 ``<MMFFC_HOME>/backups/<UTC-timestamp>/``。

AI 生成的 JSON/NBT/体素数据不可信，必须经过本引擎
校验后才能落盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from mmffc.core.errors import ConflictError, SchemaError
from mmffc.formats import fancymenu as fancymenu_format
from mmffc.formats import ftbquests as ftbquests_format
from mmffc.formats.schemas import SCHEMAS
from mmffc.io.atomic import atomic_write_bytes

TARGETS = ("fancymenu", "ftbquests")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class ApplyResult:
    ok: bool
    path: Path | None = None
    backup: Path | None = None
    validation: ValidationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": str(self.path) if self.path else None,
            "backup": str(self.backup) if self.backup else None,
            "validation": (
                self.validation.to_dict() if self.validation else None
            ),
        }


class FileIOEngine:
    """Two-phase validation + atomic write engine."""

    def __init__(self, backup_root: Path) -> None:
        self.backup_root = Path(backup_root)

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(self, target: str, content: str) -> ValidationResult:
        """Parse and validate content for the given target format."""
        if target not in TARGETS:
            raise SchemaError(f"未知的目标格式: {target!r}")

        # --- parse (syntax errors are schema errors) ------------------
        if target == "fancymenu":
            try:
                document = fancymenu_format.parse_layout(content)
            except SchemaError as exc:
                return ValidationResult(ok=False, errors=[exc.message])
            plain = document.to_dict()
            schema = SCHEMAS["fancymenu"]
        else:
            try:
                document = ftbquests_format.parse_chapter(content)
            except SchemaError as exc:
                return ValidationResult(ok=False, errors=[exc.message])
            plain = document
            schema = SCHEMAS["ftbquests"]

        # --- phase 1: JSON Schema structural validation ---------------
        errors: list[str] = []
        warnings: list[str] = []
        try:
            jsonschema.validate(plain, schema)
        except jsonschema.ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            errors.append(f"结构校验失败 [{location}]: {exc.message}")
        except jsonschema.SchemaError as exc:
            errors.append(f"内部错误: Schema 本身无效: {exc.message}")

        # --- phase 2: semantic validation -----------------------------
        if target == "fancymenu":
            semantic = fancymenu_format.validate_layout(document)
        else:
            semantic = ftbquests_format.validate_chapter(document)
        for message in semantic:
            # format modules report forward-compatibility notes as
            # "警告:" prefixed strings
            if message.startswith("警告: "):
                warnings.append(message[3:])
            else:
                errors.append(message)

        return ValidationResult(
            ok=not errors,
            errors=errors,
            warnings=warnings,
            document=document,
        )

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------
    def apply(
        self,
        target: str,
        content: str,
        dest: Path,
        *,
        backup: bool = True,
        dry_run: bool = False,
    ) -> ApplyResult:
        """Validate content and atomically write it into dest.

        dest is always treated as a directory (created when
        missing); the final file name is derived from the
        document itself.
        """
        validation = self.validate(target, content)
        if not validation.ok:
            return ApplyResult(ok=False, validation=validation)

        if target == "fancymenu":
            filename = fancymenu_format.default_filename(
                validation.document
            )
        else:
            fmt = ftbquests_format.detect_format(content)
            filename = ftbquests_format.default_filename(
                validation.document, fmt=fmt
            )

        path = dest / filename

        if dry_run:
            return ApplyResult(
                ok=True, path=path, validation=validation
            )

        data = content.encode("utf-8")
        if path.exists() and not backup:
            raise ConflictError(f"目标文件已存在: {path}")
        written_backup = None
        if backup:
            written_backup = atomic_write_bytes(
                path, data, backup_root=self.backup_root
            )
        else:
            atomic_write_bytes(path, data)

        return ApplyResult(
            ok=True,
            path=path,
            backup=written_backup,
            validation=validation,
        )

    # ------------------------------------------------------------------
    # reading / node access
    # ------------------------------------------------------------------
    def read_document(self, target: str, path: Path) -> Any:
        content = path.read_text(encoding="utf-8")
        return self.validate(target, content).document

    def document_to_plain(self, target: str, document: Any) -> Any:
        """Convert a typed document to its plain (JSON) view."""
        if target == "fancymenu":
            return document.to_dict()
        return document

    def document_from_plain(self, target: str, plain: Any) -> Any:
        """Rebuild a typed document from its plain view."""
        if target == "fancymenu":
            return fancymenu_format.from_plain(plain)
        return plain

    def get_node(
        self, target: str, document: Any, path: str
    ) -> Any:
        """Access a node by dotted path (e.g. 'sections.0.properties.x')."""
        node = document
        for part in path.split("."):
            if isinstance(node, dict):
                if part not in node:
                    raise SchemaError(f"路径不存在: {path} (缺少键 {part!r})")
                node = node[part]
            elif isinstance(node, list):
                if not part.isdigit():
                    raise SchemaError(
                        f"路径不存在: {path} ({part!r} 不是数组下标)"
                    )
                index = int(part)
                if index >= len(node):
                    raise SchemaError(
                        f"路径不存在: {path} (下标越界 {index})"
                    )
                node = node[index]
            else:
                raise SchemaError(
                    f"路径不存在: {path} ({part!r} 无法继续向下访问)"
                )
        return node

    def set_node(
        self, target: str, document: Any, path: str, value: Any
    ) -> Any:
        """Set a node by dotted path, returning a new document."""
        parts = path.split(".")
        if not parts or not parts[0]:
            raise SchemaError("空的节点路径")

        def _set(node: Any, remaining: list[str]) -> Any:
            if len(remaining) == 1:
                key = remaining[0]
                if isinstance(node, dict):
                    node = dict(node)
                    node[key] = value
                    return node
                if isinstance(node, list) and key.isdigit():
                    node = list(node)
                    index = int(key)
                    if index >= len(node):
                        raise SchemaError(f"数组下标越界: {index}")
                    node[index] = value
                    return node
                raise SchemaError(f"无法写入路径: {path}")
            key = remaining[0]
            if isinstance(node, dict):
                if key not in node:
                    raise SchemaError(
                        f"路径不存在: {path} (缺少键 {key!r})"
                    )
                child = _set(node[key], remaining[1:])
                return {**node, key: child}
            if isinstance(node, list) and key.isdigit():
                index = int(key)
                if index >= len(node):
                    raise SchemaError(f"数组下标越界: {index}")
                child = _set(node[index], remaining[1:])
                return [*node[:index], child, *node[index + 1 :]]
            raise SchemaError(f"无法写入路径: {path}")

        return _set(document, parts)
