"""mmffc mod — 模组管理 (Modrinth / CurseForge)。

确定性逻辑：搜索、版本选择、依赖解析、原子安装。
AI 内容不经过此模块。
"""

from __future__ import annotations

from pathlib import Path

import click

from mmffc.cli import mmffc_command, mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import (
    ConflictError,
    DependencyError,
    UsageError,
)
from mmffc.core.tty import confirm, is_tty, require_yes
from mmffc.io.atomic import move_to_backup
from mmffc.mods.curseforge import CurseForgeClient, normalize_hit as cf_normalize
from mmffc.mods.deps import analyze_dependencies
from mmffc.mods.manifest import InstallManifest
from mmffc.mods.modrinth import ModrinthClient, normalize_hit
from mmffc.mods.registry import execute_install, plan_install, plan_update
from mmffc.mods.scanner import scan_mods_dir


@mmffc_group()
def mod(ctx, **_kwargs) -> None:
    """模组管理：搜索、下载、依赖解析。"""


def _modrinth(ctx: click.Context) -> ModrinthClient:
    settings: CliContext = ctx.obj
    return ModrinthClient(token=settings.config.modrinth_token)


def _curseforge(ctx: click.Context) -> CurseForgeClient:
    settings: CliContext = ctx.obj
    api_key = settings.config.curseforge_api_key
    if not api_key:
        raise UsageError(
            "CurseForge 需要 API Key：设置环境变量 CURSEFORGE_API_KEY "
            "或在配置文件中写入 curseforge_api_key"
        )
    return CurseForgeClient(api_key=api_key)


@mod.command("search")
@click.argument("query", required=False)
@click.option("--loader", type=click.Choice(["fabric", "forge", "quilt", "neoforge"]), default=None, help="按加载器过滤")
@click.option("--mc-version", "mc_version", default=None, help="按游戏版本过滤")
@click.option("--category", default=None, help="按 Modrinth 分类过滤")
@click.option("--limit", type=int, default=20, show_default=True, help="结果数量上限")
@click.option("--offset", type=int, default=0, show_default=True)
@click.option("--source", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", show_default=True)
def mod_search(ctx, query, loader, mc_version, category, limit, offset, source):
    """搜索模组。"""
    settings: CliContext = ctx.obj
    if source == "modrinth":
        facets = [["project_type:mod"]]
        if loader:
            facets.append([f"loaders:{loader}"])
        if mc_version:
            facets.append([f"versions:{mc_version}"])
        if category:
            facets.append([f"categories:{category}"])
        client = _modrinth(ctx)
        result = client.search(query, facets=facets, limit=limit, offset=offset)
        hits = [normalize_hit(hit) for hit in result.get("hits", [])]
        settings.renderer.info(f"命中 {len(hits)} 个项目 (总命中 {result.get('total_hits', 0)})")
        settings.renderer.emit(hits)
    else:
        client = _curseforge(ctx)
        result = client.search_mods(
            query,
            game_version=mc_version,
            mod_loader=loader,
            page_size=min(limit, 50),
            index=offset,
        )
        hits = [cf_normalize(entry) for entry in result.get("data", [])]
        total = result.get("pagination", {}).get("totalCount", 0)
        settings.renderer.info(f"命中 {len(hits)} 个项目 (总命中 {total})")
        settings.renderer.emit(hits)


@mod.command("info")
@click.argument("ref")
@click.option("--source", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", show_default=True)
def mod_info(ctx, ref, source):
    """显示模组项目详情与版本列表。"""
    settings: CliContext = ctx.obj
    if source == "modrinth":
        client = _modrinth(ctx)
        project = client.get_project(ref)
        versions = client.get_versions(project["id"])
        settings.renderer.emit(
            {
                "project": project,
                "versions": [
                    {
                        "id": v.get("id"),
                        "name": v.get("name"),
                        "version_number": v.get("version_number"),
                        "version_type": v.get("version_type"),
                        "game_versions": v.get("game_versions"),
                        "loaders": v.get("loaders"),
                        "date_published": v.get("date_published"),
                        "files": v.get("files"),
                        "dependencies": v.get("dependencies"),
                    }
                    for v in versions
                ],
            }
        )
    else:
        if not str(ref).isdigit():
            raise UsageError(f"CurseForge 需要数字 mod id，得到: {ref}")
        client = _curseforge(ctx)
        mod = client.get_mod(int(ref))
        settings.renderer.emit(mod.get("data", mod))


@mod.command("install")
@click.argument("ref", required=False)
@click.option("--version", "version_number", default=None, help="精确版本号 (version_number)")
@click.option("--loader", type=click.Choice(["fabric", "forge", "quilt", "neoforge"]), default=None)
@click.option("--mc-version", "mc_version", default=None)
@click.option("--resolve-deps", "resolve_deps", is_flag=True, help="递归解析并安装必需依赖")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
@click.option("--stdin", "use_stdin", is_flag=True, help="从 stdin 读取 project_id/slug，每行一个")
@click.option("--source", type=click.Choice(["modrinth", "curseforge"]), default="modrinth", show_default=True)
def mod_install(ctx, ref, version_number, loader, mc_version, resolve_deps, mods_dir_opt, use_stdin, source):
    """安装模组到 mods 目录（原子写入 + 备份）。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir

    refs: list[str] = []
    if use_stdin:
        for line in click.get_text_stream("stdin"):
            line = line.strip()
            if line and not line.startswith("#"):
                refs.append(line)
    elif ref:
        refs.append(ref)
    else:
        raise UsageError("需要提供 project_id/slug 参数，或使用 --stdin 从标准输入读取")

    if source == "curseforge":
        raise UsageError("CurseForge 安装暂不支持 (--source modrinth 可用)")

    # 高风险操作：非 TTY 必须 --yes
    if not settings.dry_run:
        require_yes(settings.yes, "mod install")

    client = _modrinth(ctx)
    plan_records = []
    all_results = []
    for one_ref in refs:
        plan = plan_install(
            client,
            one_ref,
            version_number=version_number,
            mc_version=mc_version,
            loader=loader,
            resolve_deps=resolve_deps,
            mods_dir=mods_dir,
        )
        plan_records.append(plan.to_dict())
        if settings.dry_run:
            continue
        force = settings.yes
        if plan.conflicts and not force and is_tty():
            force = confirm(
                f"检测到版本冲突，是否覆盖旧版本并备份？ ({len(plan.conflicts)} 个冲突)",
                default=False,
            )
        if plan.conflicts and not force:
            raise ConflictError(
                "存在版本冲突，使用 --yes 覆盖并自动备份旧文件",
                conflicts=plan.conflicts,
            )
        results = execute_install(
            client,
            plan,
            mods_dir,
            backup_root=settings.config.backups_dir,
            force=force,
        )
        all_results.extend(results)

    if settings.dry_run:
        settings.renderer.emit({"dry_run": True, "plans": plan_records})
    else:
        skipped = [s for p in plan_records for s in p["skipped"]]
        settings.renderer.emit({"installed": all_results, "skipped": skipped})


@mod.command("list")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
def mod_list(ctx, mods_dir_opt):
    """列出本地 mods 目录中的模组。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir
    manifest = InstallManifest.load(mods_dir)
    mod_files = scan_mods_dir(mods_dir)
    rows = []
    for mod_file in mod_files:
        primary = mod_file.primary
        entry = manifest.find(mod_file.filename)
        row = {
            "file": mod_file.filename,
            "loader": mod_file.loader,
            "mod_id": primary.mod_id if primary else None,
            "name": primary.name if primary else None,
            "version": primary.version if primary else None,
            "error": mod_file.error,
        }
        if entry:
            slug, manifest_entry = entry
            row["managed"] = manifest_entry.get("slug")
            row["remote_version"] = manifest_entry.get("version")
        rows.append(row)
    settings.renderer.emit(rows)


@mod.command("scan")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
def mod_scan(ctx, mods_dir_opt):
    """扫描 mods 目录并解析依赖清单。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir
    mod_files = scan_mods_dir(mods_dir)
    settings.renderer.info(f"扫描 {len(mod_files)} 个 JAR 文件")
    settings.renderer.emit([mf.to_dict() for mf in mod_files])


@mod.command("deps")
@click.option("--tree", "tree", is_flag=True, help="树状输出")
@click.option("--missing", "missing_only", is_flag=True, help="仅显示缺失的必需依赖")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
def mod_deps(ctx, tree, missing_only, mods_dir_opt):
    """分析本地依赖图。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir
    mod_files = scan_mods_dir(mods_dir)
    reports = analyze_dependencies(mod_files)

    if missing_only:
        missing = sorted({m for r in reports for m in r.missing})
        settings.renderer.emit({"missing": missing, "count": len(missing)})
        return

    if tree:
        for report in reports:
            click.echo(f"{report.mod_id} ({report.name or '?'}) [{report.loader or '?'}]")
            for dep_id in report.present:
                click.echo(f"  ├── {dep_id} (已安装)")
            for dep_id in report.missing:
                click.echo(f"  ├── {dep_id} (缺失!)")
            for dep_id in report.optional_missing:
                click.echo(f"  └── {dep_id} (可选, 缺失)")
        return

    settings.renderer.emit([r.to_dict() for r in reports])


@mod.command("remove")
@click.argument("ref")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
def mod_remove(ctx, ref, mods_dir_opt):
    """移除已安装模组（移动到备份目录）。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir

    if not settings.dry_run:
        require_yes(settings.yes, "mod remove")

    manifest = InstallManifest.load(mods_dir)
    found = manifest.find(ref)
    target_file: Path | None = None
    entry = None
    if found:
        slug, entry = found
        target_file = mods_dir / entry["file"]
    else:
        # 回退：按 mod id / 名称扫描匹配
        for mod_file in scan_mods_dir(mods_dir):
            primary = mod_file.primary
            if primary and ref.lower() in {
                (primary.mod_id or "").lower(),
                (primary.name or "").lower(),
                mod_file.filename.lower(),
            }:
                target_file = mod_file.path
                break

    if target_file is None or not target_file.exists():
        raise UsageError(f"未找到已安装的模组: {ref}")

    if settings.dry_run:
        settings.renderer.emit({"dry_run": True, "would_remove": str(target_file)})
        return

    backup = move_to_backup(target_file, settings.config.backups_dir)
    if entry:
        manifest.remove(entry.get("slug") or ref)
        manifest.save()
    settings.renderer.emit(
        {"removed": str(target_file), "backup": str(backup) if backup else None}
    )


@mod.command("update")
@click.option("--all", "all_versions", is_flag=True, help="包含预发布版本 (beta/alpha)")
@click.option("--mods-dir", "mods_dir_opt", type=click.Path(file_okay=False), default=None)
def mod_update(ctx, all_versions, mods_dir_opt):
    """检查并更新已安装模组。"""
    settings: CliContext = ctx.obj
    mods_dir = Path(mods_dir_opt).expanduser() if mods_dir_opt else settings.config.mods_dir

    client = _modrinth(ctx)
    updates = plan_update(client, mods_dir, include_prerelease=all_versions)
    if not updates:
        settings.renderer.emit({"updates": [], "count": 0})
        return

    if settings.dry_run:
        settings.renderer.emit({"dry_run": True, "updates": updates, "count": len(updates)})
        return

    require_yes(settings.yes, "mod update")

    results = []
    for update in updates:
        plan = plan_install(
            client,
            update["slug"],
            version_number=update["latest"],
            resolve_deps=False,
            mods_dir=mods_dir,
        )
        results.extend(
            execute_install(
                client,
                plan,
                mods_dir,
                backup_root=settings.config.backups_dir,
                force=True,
            )
        )
    settings.renderer.emit({"updates": results, "count": len(results)})
