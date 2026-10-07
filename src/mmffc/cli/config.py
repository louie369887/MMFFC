"""mmffc config — 配置校验与写入（file_io_engine 的 CLI 入口）。

CLI 不生成 AI 内容，只接收 stdin 或文件，
做两阶段校验（JSON Schema + 语义校验）后原子写入。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import SchemaError
from mmffc.core.tty import require_yes
from mmffc.io.file_io import TARGETS, FileIOEngine


@mmffc_group()
def config(ctx, **_kwargs) -> None:
    """配置校验与写入（FancyMenu / FTB Quests）。"""


def _engine(ctx: click.Context) -> FileIOEngine:
    settings: CliContext = ctx.obj
    return FileIOEngine(settings.config.backups_dir)


def _read_input(file: str) -> str:
    if file == "-":
        return click.get_text_stream("stdin").read()
    return Path(file).read_text(encoding="utf-8")


@config.command("validate")
@click.argument("target", type=click.Choice(TARGETS))
@click.argument("file", default="-")
@click.option("--schema", "schema_path", type=click.Path(exists=True), default=None, help="自定义 JSON Schema（覆盖内置）")
def config_validate(ctx, target, file, schema_path):
    """校验配置内容（两阶段：结构 + 语义）。退出码 3 = 校验失败。"""
    settings: CliContext = ctx.obj
    engine = _engine(ctx)

    if schema_path:
        import jsonschema

        with open(schema_path, encoding="utf-8") as fh:
            custom = json.load(fh)
        content = _read_input(file)
        if target == "fancymenu":
            from mmffc.formats import fancymenu as fmt

            try:
                document = fmt.parse_layout(content)
                plain = document.to_dict()
            except SchemaError as exc:
                settings.renderer.emit(
                    {"ok": False, "errors": [exc.message], "warnings": []}
                )
                raise SystemExit(3) from None
        else:
            from mmffc.formats import ftbquests as fmt

            try:
                document = fmt.parse_chapter(content)
                plain = document
            except SchemaError as exc:
                settings.renderer.emit(
                    {"ok": False, "errors": [exc.message], "warnings": []}
                )
                raise SystemExit(3) from None
        errors: list[str] = []
        try:
            jsonschema.validate(plain, custom)
        except jsonschema.ValidationError as exc:
            location = ".".join(str(p) for p in exc.absolute_path) or "$"
            errors.append(f"结构校验失败 [{location}]: {exc.message}")
        if target == "fancymenu":
            from mmffc.formats import fancymenu as fmt

            errors.extend(fmt.validate_layout(document))
        else:
            from mmffc.formats import ftbquests as fmt

            errors.extend(fmt.validate_chapter(document))
        ok = not errors
        settings.renderer.emit({"ok": ok, "errors": errors, "warnings": []})
        if not ok:
            raise SystemExit(3)
        return

    content = _read_input(file)
    result = engine.validate(target, content)
    settings.renderer.emit(result.to_dict())
    if not result.ok:
        raise SystemExit(3)


@config.command("apply")
@click.argument("target", type=click.Choice(TARGETS))
@click.argument("file", default="-")
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), required=True, help="目标配置目录")
@click.option("--backup", is_flag=True, default=True, help="写入前备份（默认启用）")
def config_apply(ctx, target, file, dest_dir, backup):
    """校验并原子写入配置到目标目录。"""
    settings: CliContext = ctx.obj
    engine = _engine(ctx)
    dest = Path(dest_dir).expanduser()

    dry_run = settings.dry_run
    content = _read_input(file)

    # 预校验：dry-run 与正式执行共用
    validation = engine.validate(target, content)
    if not validation.ok:
        settings.renderer.emit(
            {
                "ok": False,
                "errors": validation.errors,
                "warnings": validation.warnings,
            }
        )
        raise SystemExit(3)

    if not dry_run:
        require_yes(settings.yes, "config apply")

    result = engine.apply(
        target,
        content,
        dest,
        backup=backup,
        dry_run=dry_run,
    )
    settings.renderer.emit(result.to_dict())


@config.command("get")
@click.argument("target", type=click.Choice(TARGETS))
@click.argument("path")
@click.option("--dest", "dest_file", type=click.Path(exists=True, dir_okay=False), required=True, help="配置文件路径")
def config_get(ctx, target, path, dest_file):
    """读取配置文档中的节点（点分路径，如 sections.0.properties.x）。"""
    settings: CliContext = ctx.obj
    engine = _engine(ctx)
    document = engine.read_document(target, Path(dest_file).expanduser())
    plain = engine.document_to_plain(target, document)
    try:
        node = engine.get_node(target, plain, path)
    except SchemaError as exc:
        raise click.ClickException(exc.message) from None
    settings.renderer.emit(node)


@config.command("set")
@click.argument("target", type=click.Choice(TARGETS))
@click.argument("path")
@click.argument("value")
@click.option("--dest", "dest_file", type=click.Path(exists=True, dir_okay=False), required=True, help="配置文件路径")
def config_set(ctx, target, path, value, dest_file):
    """设置配置文档中的节点并原子写回。"""
    settings: CliContext = ctx.obj
    engine = _engine(ctx)
    dest = Path(dest_file).expanduser()

    require_yes(settings.yes, "config set")

    document = engine.read_document(target, dest)
    plain = engine.document_to_plain(target, document)
    parsed = _parse_value(value)
    try:
        updated_plain = engine.set_node(target, plain, path, parsed)
    except SchemaError as exc:
        raise click.ClickException(exc.message) from None

    updated = engine.document_from_plain(target, updated_plain)

    if target == "fancymenu":
        from mmffc.formats import fancymenu as fmt

        content = fmt.serialize_layout(updated)
        errors = fmt.validate_layout(updated)
    else:
        from mmffc.formats import ftbquests as fmt

        fmt_detected = fmt.detect_format(dest.read_text(encoding="utf-8"))
        content = fmt.serialize_chapter(updated, fmt=fmt_detected)
        errors = fmt.validate_chapter(updated)

    errors = [e for e in errors if not e.startswith("警告: ")]
    if errors:
        raise click.ClickException(
            "修改后未通过语义校验: " + "; ".join(errors)
        )

    from mmffc.io.atomic import atomic_write_bytes

    backup = atomic_write_bytes(dest, content.encode("utf-8"), backup_root=settings.config.backups_dir)
    settings.renderer.emit(
        {
            "ok": True,
            "path": str(dest),
            "backup": str(backup) if backup else None,
            "node": path,
            "value": parsed,
        }
    )


@config.command("diff")
@click.argument("target", type=click.Choice(TARGETS))
@click.argument("file", default="-")
@click.option("--dest", "dest_file", type=click.Path(exists=True, dir_okay=False), required=True, help="对比目标文件")
def config_diff(ctx, target, file, dest_file):
    """对比 stdin/文件内容与目标文件的差异。"""
    settings: CliContext = ctx.obj
    engine = _engine(ctx)

    incoming = _read_input(file)
    existing = Path(dest_file).expanduser().read_text(encoding="utf-8")

    def _parse(text: str):
        try:
            if target == "fancymenu":
                from mmffc.formats import fancymenu as fmt

                return fmt.parse_layout(text).to_dict()
            from mmffc.formats import ftbquests as fmt

            return fmt.parse_chapter(text)
        except SchemaError as exc:
            raise click.ClickException(exc.message) from None

    incoming_doc = _parse(incoming)
    existing_doc = _parse(existing)

    changes = _diff_docs(existing_doc, incoming_doc)
    settings.renderer.emit(
        {"same": not changes, "changes": changes, "count": len(changes)}
    )


def _parse_value(value: str):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _diff_docs(existing: dict, incoming: dict) -> list[dict]:
    """Compute a shallow-ish diff between two plain documents."""
    import json

    changes: list[dict] = []
    existing_json = json.dumps(existing, sort_keys=True, ensure_ascii=False)
    incoming_json = json.dumps(incoming, sort_keys=True, ensure_ascii=False)
    if existing_json == incoming_json:
        return changes

    existing_flat = _flatten(existing)
    incoming_flat = _flatten(incoming)
    keys = sorted(set(existing_flat) | set(incoming_flat))
    for key in keys:
        old = existing_flat.get(key)
        new = incoming_flat.get(key)
        if old != new:
            changes.append(
                {"path": key, "before": old, "after": new}
            )
    return changes


def _flatten(doc: Any, prefix: str = "") -> dict:
    result: dict = {}
    if isinstance(doc, dict):
        for key, value in doc.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value, path))
    elif isinstance(doc, list):
        for index, value in enumerate(doc):
            path = f"{prefix}.{index}"
            result.update(_flatten(value, path))
    else:
        result[prefix] = doc
    return result
