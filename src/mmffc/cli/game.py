"""mmffc game — 崩溃诊断与版本保活（Phase 6，CLI.md 3.8）。

只读诊断（log / crash / diagnose / list）+ 真实启动验证（verify）。
CLI 不调用 LLM；归因由确定性特征匹配完成。

证据路径（确定性，不递归猜测）::

    <DIR>/logs/latest.log, debug.log, <yyyy-MM-dd>-<n>.log.gz
    <DIR>/crash-reports/crash-<...>.txt
    <DIR>/hs_err_pid<PID>.log
    <DIR>/launcher_log.txt
"""

from __future__ import annotations

from pathlib import Path

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.core.errors import DependencyError, UsageError
from mmffc.core.tty import require_yes
from mmffc.game.attribution import classify, severity_for
from mmffc.game.crash import latest_crash, latest_jvm_crash
from mmffc.game.logs import LEVEL_CHOICES, scan_source, select_entries
from mmffc.game.verify import (
    LOADERS,
    VerifyError,
    build_plan,
    cleanup,
    verify_version,
)

SOURCE_CHOICES = ["latest", "debug", "history", "all"]


@mmffc_group()
def game(ctx, **_kwargs) -> None:
    """崩溃诊断（log/crash/diagnose）与版本保活验证（verify）。"""


def _game_dir(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_dir():
        raise UsageError(f"目录不存在: {path}")
    return path


@game.command("log")
@click.argument("dir", type=click.Path(file_okay=False))
@click.option("--tail", type=int, default=None, help="只保留最后 N 条")
@click.option(
    "--level",
    type=click.Choice(LEVEL_CHOICES),
    default="info",
    help="最低严重级别（默认 info，即含 warn/error）",
)
@click.option(
    "--source",
    type=click.Choice(SOURCE_CHOICES),
    default="latest",
    help="日志来源（默认 latest）",
)
def game_log(ctx, dir, tail, level, source):
    """解析实例日志，定位 ERROR/FATAL 及其证据行。"""
    settings: CliContext = ctx.obj
    game_dir = _game_dir(dir)
    scans = scan_source(game_dir, source)
    entries = select_entries(scans, min_level=level, tail=tail)
    settings.renderer.emit(
        {
            "ok": True,
            "game_dir": str(game_dir),
            "source": source,
            "level": level,
            "entry_count": len(entries),
            "files": [str(scan.path) for scan in scans if scan.path],
            "entries": entries,
        }
    )


@game.command("crash")
@click.argument("dir", type=click.Path(file_okay=False))
@click.option("--latest", "latest", is_flag=True, default=True,
              help="读取最新崩溃报告（默认）")
@click.option("--file", "file", type=click.Path(), default=None,
              help="指定单个崩溃报告文件")
def game_crash(ctx, dir, latest, file):
    """解析崩溃报告：Description、堆栈顶部帧、可疑模组。"""
    settings: CliContext = ctx.obj
    game_dir = _game_dir(dir)
    if file is not None:
        path = Path(file)
        if not path.is_file():
            raise UsageError(f"崩溃报告不存在: {path}")
        report = latest_crash(game_dir, file=path)
    else:
        report = latest_crash(game_dir)
    settings.renderer.emit({"ok": True, "game_dir": str(game_dir), "crash": report.to_dict()})


@game.command("diagnose")
@click.argument("dir", type=click.Path(file_okay=False))
def game_diagnose(ctx, dir):
    """综合诊断：logs + crash-reports + hs_err -> 结构化归因。"""
    settings: CliContext = ctx.obj
    game_dir = _game_dir(dir)

    scans = scan_source(game_dir, "latest")
    log_entries = [entry for scan in scans for entry in scan.entries]
    problem_entries = [
        entry
        for entry in log_entries
        if entry.is_problem
        or "Exception" in entry.message
        or "Mixin" in entry.message
        or "Could not load" in entry.message
    ]
    problem_lines = [
        ("latest.log", entry.lineno, entry.message)
        for entry in problem_entries
    ]

    report = latest_crash(game_dir)
    crash_lines: list[tuple[str, int, str]] = [
        ("crash-report", index, line)
        for index, line in enumerate(report.frames + report.caused_by, start=1)
    ]
    if report.exception:
        crash_lines.append(("crash-report", 0, report.exception))

    jvm = latest_jvm_crash(game_dir)
    jvm_text = (
        "\n".join(jvm.summary)
        if jvm.found and (jvm.summary or jvm.problem_frame)
        else None
    )
    signature = classify(
        problem_lines,
        crash_lines=crash_lines or None,
        jvm_text=jvm_text,
    )
    has_problems = bool(problem_entries) or report.found or jvm.found
    severity = severity_for(signature.code) if has_problems else "ok"

    settings.renderer.emit(
        {
            "ok": True,
            "game_dir": str(game_dir),
            "severity": severity,
            "attribution": signature.code if has_problems else None,
            "attribution_title": signature.title if has_problems else None,
            "suggestion": signature.suggestion if has_problems else "未发现错误",
            "evidence": signature.evidence if has_problems else [],
            "crash": report.to_dict(),
            "jvm": jvm.to_dict(),
            "log_summary": {
                "entries": len(log_entries),
                "errors": sum(1 for entry in log_entries if entry.is_problem),
                "warnings": sum(
                    1 for entry in log_entries if entry.level == "WARN"
                ),
                "files": [str(scan.path) for scan in scans if scan.path],
            },
        }
    )


@game.command("verify")
@click.argument("dir", type=click.Path(file_okay=False))
@click.option(
    "--loader",
    type=click.Choice(LOADERS),
    default="auto",
    help="服务端载体类型（默认 auto 探测）",
)
@click.option("--version", "version", default=None, help="MC 版本（定位 jar 用）")
@click.option("--java", "java", default=None, help="JVM 路径（默认 --java/环境变量/PATH）")
@click.option("--memory", default="1024M", help="JVM 最大堆（默认 1024M）")
@click.option("--timeout", type=float, default=300.0, help="启动超时秒数")
@click.option("--keep", type=float, default=0.0, help="命中存活标志后的保活观察秒数")
@click.option("--probe", is_flag=True, help="保活期间探测端口连通性")
@click.option("--dry-run", is_flag=True, help="只输出执行计划，不启动")
def game_verify(ctx, dir, loader, version, java, memory, timeout, keep, probe, dry_run):
    """启动该版本并验证保活性（等 "Done (…)! For help, type"）。"""
    settings: CliContext = ctx.obj
    game_dir = _game_dir(dir)

    try:
        if dry_run:
            plan = build_plan(
                game_dir, loader=loader, version=version, java=java,
                memory=memory,
            )
            settings.renderer.emit(plan.to_dict())
            return
        plan = build_plan(
            game_dir, loader=loader, version=version, java=java, memory=memory
        )
    except VerifyError as exc:
        raise DependencyError(str(exc)) from None

    require_yes(settings.yes, f"game verify {game_dir}")
    result = verify_version(
        game_dir,
        loader=loader,
        version=version,
        java=java,
        memory=memory,
        timeout=timeout,
        keep=keep,
        probe=probe,
    )
    payload = result.to_dict()
    settings.renderer.emit(payload)
    if result.status == "alive":
        cleanup(result.workdir)  # 成功即清理临时目录
        return
    cleanup(result.workdir)
    raise DependencyError(
        f"版本保活失败: {result.status}（阶段 {result.phase}"
        + (f"，归因 {result.attribution_title or result.attribution}"
           if result.attribution else "")
        + f"，详见 {result.log_path or '临时日志已清理'}）"
    )


@game.command("list")
@click.option("--dir", "parent", default=None, help="包含多个实例的父目录")
def game_list(ctx, parent):
    """列出实例的日志/崩溃报告证据情况（diagnose 前的路径确认）。"""
    from mmffc.game.crash import find_crash_reports, find_hs_err_files
    from mmffc.game.logs import discover_logs

    settings: CliContext = ctx.obj
    targets: list[Path] = []
    if parent is None:
        raise UsageError("请提供 --dir（包含实例目录的父目录）")
    base = _game_dir(parent)
    targets = [child for child in sorted(base.iterdir()) if child.is_dir()]
    if not targets:
        targets = [base]

    instances = []
    for target in targets:
        logs = discover_logs(target)
        instances.append(
            {
                "dir": str(target),
                "latest_log": bool(logs["latest"]),
                "debug_log": bool(logs["debug"]),
                "history_logs": len(logs["history"]),
                "crash_reports": len(find_crash_reports(target)),
                "jvm_errors": len(find_hs_err_files(target)),
            }
        )
    settings.renderer.emit(
        {"ok": True, "parent": str(base), "instance_count": len(instances),
         "instances": instances}
    )
