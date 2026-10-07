"""mmffc shell — 可选交互式 REPL (DOS 风格交互层)。

Unix 管道自动化不经过这里；shell 内部仍调用相同的 click 命令。
"""

from __future__ import annotations

import shlex

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory

from mmffc.cli import cli, mmffc_command
from mmffc.core.context import CliContext
from mmffc.core.errors import MMFFCError

COMMAND_WORDS = {
    "mod": "模组管理",
    "config": "配置校验与写入",
    "structure": "结构文件生成",
    "world": "MCA 编译与世界生成",
    "rcon": "游戏内热重载",
    "cache": "缓存管理",
    "shell": "交互式 REPL",
    "help": "帮助",
    "exit": "退出",
    "quit": "退出",
    # mod verbs
    "search": "搜索",
    "info": "详情",
    "install": "安装",
    "list": "列出",
    "scan": "扫描",
    "deps": "依赖分析",
    "remove": "移除",
    "update": "更新",
    # global options
    "--json": "JSON 输出",
    "--ndjson": "NDJSON 输出",
    "--format": "输出格式",
    "--dry-run": "仅打印计划",
    "--yes": "跳过确认",
    "-y": "跳过确认",
    "--no-color": "禁用颜色",
    "--quiet": "安静模式",
    "-q": "安静模式",
    "--verbose": "详细日志",
    "-v": "详细日志",
    "--cwd": "工作目录",
    "--config": "配置文件",
    "--log-level": "日志级别",
}


@mmffc_command()
@click.option("--no-history", is_flag=True, help="不记录历史")
def shell(ctx, no_history):
    """交互式 shell（可选；自动化请使用管道）。"""
    settings: CliContext = ctx.obj

    history_path = settings.config.home / "history"
    if no_history:
        history = None
    else:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(history_path))

    completer = WordCompleter(
        list(COMMAND_WORDS),
        meta_dict=COMMAND_WORDS,
        ignore_case=True,
        sentence=False,
    )

    session: PromptSession = PromptSession(
        history=history,
        completer=completer,
        complete_while_typing=True,
    )

    console = settings.renderer.console
    console.print(
        "[bold cyan]MMFFC shell[/bold cyan] — 输入命令，exit 退出。"
    )

    while True:
        try:
            line = session.prompt("mmffc> ")
        except (KeyboardInterrupt, EOFError):
            console.print()
            break

        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            break

        try:
            args = shlex.split(line)
        except ValueError as exc:
            console.print(f"[red]解析错误:[/red] {exc}")
            continue

        try:
            code = cli.main(args=args, prog_name="mmffc", standalone_mode=False)
        except click.ClickException as exc:
            exc.show()
            code = exc.exit_code
        except click.Abort:
            code = 130
        except MMFFCError as exc:
            console.print(f"[red]错误:[/red] {exc.message}")
            code = exc.exit_code
        except KeyboardInterrupt:
            console.print()
            code = 130
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0

        if code:
            console.print(f"[dim]退出码: {code}[/dim]")
