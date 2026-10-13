"""MMFFC 统一入口（launcher）。

一个入口三种形态：

* ``mmffc``（无参数）→ 打印 ASCII 立体字横幅并进入 ``mmffc>`` 交互提示符
  （DOS / cmd 风格，命令省略 ``mmffc`` 前缀）
* ``mmffc <命令...>``（有参数）→ 一次性执行同名 CLI 子命令，
  即 CLI.md 中的命令去掉 ``mmffc`` 前缀后的剩余部分
* ``mmffc --mcp``（或交互内输入 ``mcp``）→ 以 stdio 启动 MCP Server

Windows 上的 ``mmffc.exe``（PyInstaller onefile）与本模块行为一致。
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Any, Callable

BANNER_WORD = "MMFFC"

GLYPHS = {
    "M": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "F": ["████", "█   ", "███ ", "█   ", "█   "],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
}
FACE = "█"
SHADE = "▒"
PROMPT = "mmffc> "

# 纯 ASCII 备用字面（目标流无法编码 Unicode 时使用，避免 frozen exe 崩溃）
ASCII_FACE = "#"
ASCII_SHADE = ":"

SHELL_WORDS = [
    "help", "version", "mcp", "exit", "quit", "banner",
    "mod", "config", "structure", "world", "game", "rcon", "cache",
    "search", "install", "compile", "init", "build", "validate",
    "inspect", "log", "crash", "diagnose", "verify", "list", "scan",
    "deps", "update", "remove", "apply", "export",
    "--json", "--ndjson", "--dry-run", "--yes", "--format", "--quiet",
]

SHELL_HELP = """\
命令与 CLI.md 完全一致（省略 mmffc 前缀）：

  mod|config|structure|world|game|rcon|cache|mcp <verb> [options]

  help              显示完整命令树（等价 CLI.md）
  version           显示版本
  mcp               在当前终端启动 MCP Server（stdio）
  exit | quit       退出（Ctrl-C / Ctrl-D 亦可）

示例：
  mmffc> world init ./saves/test --mc-version 1.20.1
  mmffc> game diagnose ./instance/.minecraft --json
"""


def build_banner(
    word: str = BANNER_WORD, face: str = FACE, shade: str = SHADE
) -> str:
    """生成带右下挤出阴影的 ASCII 立体字（大写）。"""
    cell = 5
    gap = 1
    width = len(word) * cell + (len(word) - 1) * gap + 1
    height = len(GLYPHS["M"]) + 1
    canvas = [[" "] * width for _ in range(height)]

    for index, char in enumerate(word):
        glyph = GLYPHS.get(char)
        if glyph is None:
            continue
        x0 = index * (cell + gap)
        for row, line in enumerate(glyph):
            for column, cell_char in enumerate(line):
                if cell_char != " ":
                    canvas[row][x0 + column] = face

    extruded = [row[:] for row in canvas]
    for row in range(height):
        for column in range(width):
            if canvas[row][column] != face:
                continue
            if row + 1 < height and column + 1 < width:
                if canvas[row + 1][column + 1] != face:
                    extruded[row + 1][column + 1] = shade

    lines = ["".join(row).rstrip() for row in extruded]
    while lines and not lines[-1]:
        lines.pop()
    width = max((len(line) for line in lines), default=0)
    return "\n".join(line.ljust(width) for line in lines)


def _ensure_utf8_streams() -> None:
    """把 stdout/stderr 重配置为 UTF-8（frozen exe 默认跟随 ANSI 代码页）。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 流不可重配时保持原样
            pass


def _encoding_supports(encoding: str, char: str) -> bool:
    if not encoding:
        return False
    try:
        char.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _styled_banner() -> Any:
    """带 ANSI 配色的横幅（rich Text；不可用时用 ASCII 字面）。"""
    from rich.text import Text

    encoding = getattr(sys.stdout, "encoding", "") or ""
    if not _encoding_supports(encoding, FACE):
        return build_banner(face=ASCII_FACE, shade=ASCII_SHADE)

    art = build_banner()
    text = Text()
    offset = 0
    for line in art.split("\n"):
        text.append(line + "\n")
        for column, char in enumerate(line):
            style = "bold cyan" if char == FACE else "blue"
            text.stylize(style, offset + column, offset + column + 1)
        offset += len(line) + 1
    if os.environ.get("NO_COLOR"):
        return art
    return text


def _dispatch(args: list[str]) -> int:
    """把（去前缀的）参数交给 click CLI。"""
    import click

    from mmffc.cli import cli
    from mmffc.core.errors import MMFFCError

    try:
        code = cli.main(args=args, prog_name="mmffc", standalone_mode=False)
        return int(code or 0)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code or 1)
    except click.Abort:
        return 130
    except MMFFCError as exc:
        click.echo(f"mmffc: 错误: {exc.message}", err=True)
        return int(exc.exit_code)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1


def _run_mcp() -> int:
    from mmffc.mcp.server import main as mcp_main

    return int(mcp_main())


def _print_shell_banner(console, version: str) -> None:
    from rich.panel import Panel

    console.print(_styled_banner())
    console.print(
        Panel.fit(
            f"[bold]MMFFC[/bold] {version} — Minecraft Mod / FancyMenu / FTB / "
            "Control 自动化框架\n"
            "CLI 与 MCP 双核：本 shell 为确定性入口，MCP 服务供 AI 客户端连接。",
            border_style="cyan",
        )
    )
    console.print(SHELL_HELP)


def run_shell() -> int:
    """交互式 REPL：``mmffc>`` 提示符（命令省略 mmffc 前缀）。"""
    from mmffc import __version__
    from rich.console import Console

    console = Console()

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
    except ImportError:  # pragma: no cover - prompt_toolkit 是硬依赖
        PromptSession = None  # type: ignore[assignment]
        WordCompleter = None  # type: ignore[assignment]
        FileHistory = None  # type: ignore[assignment]

    session = None
    if PromptSession is not None and _is_interactive():
        history = None
        try:
            from mmffc.core.config import load_config

            history_path = load_config().home / "shell-history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_path))
        except Exception:  # pragma: no cover - 历史文件不可用时降级
            history = None
        try:
            session = PromptSession(history=history, completer=completer)
        except Exception:  # pragma: no cover - 无控制台时降级到 input()
            session = None

    _print_shell_banner(console, __version__)

    while True:
        try:
            if session is not None:
                line = session.prompt(PROMPT)
            else:
                console.print(PROMPT, markup=False, end="")
                line = input()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return 0

        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("exit", "quit"):
            return 0
        if stripped in ("help", "?", "commands"):
            _dispatch(["--help"])
            continue
        if stripped in ("version", "-V", "--version"):
            _dispatch(["--version"])
            continue
        if stripped == "banner":
            console.print(_styled_banner())
            continue
        if stripped == "mcp":
            console.print("[dim]MCP Server 启动中（stdio；Ctrl-C 退出）[/dim]")
            return _run_mcp()

        try:
            args = shlex.split(stripped)
        except ValueError as exc:
            console.print(f"[red]解析错误:[/red] {exc}")
            continue
        try:
            code = _dispatch(args)
        except KeyboardInterrupt:
            console.print()
            code = 130
        if code:
            console.print(f"[dim]退出码: {code}[/dim]")


def _is_interactive() -> bool:
    """仅当 stdin 与 stdout 都是真实终端时才进入交互模式。"""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):  # pragma: no cover
        return False


def main(argv: list[str] | None = None) -> int:
    """统一入口：无参进交互 shell；有参执行 CLI；--mcp 启动 MCP。"""
    _ensure_utf8_streams()
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in ("--mcp", "-M"):
        return _run_mcp()
    if args and args[0] in ("--shell", "-i"):
        return run_shell()
    if args:
        return _dispatch(args)

    if not _is_interactive():
        # 管道/重定向场景（无交互输入）：打印横幅与用法后按 CLI 单次执行处理
        from mmffc import __version__
        from rich.console import Console

        console = Console()
        console.print(_styled_banner())
        console.print(f"MMFFC {__version__} — 无交互输入，按 CLI 单次执行处理。")
        console.print("用法: mmffc <命令...> | mmffc --mcp | mmffc --help")
        return 2
    return run_shell()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
