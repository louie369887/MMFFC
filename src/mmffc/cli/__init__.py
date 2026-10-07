"""MMFFC CLI entry point.

Unix-style command tree (see CLI.md): ``mmffc <noun> <verb> [options]``.
Deterministic only: the CLI never calls an LLM; AI-generated content is
accepted via stdin/files, validated, then atomically written.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
from typing import Any, Callable, TypeVar

import click

from mmffc import __version__
from mmffc.core.context import CliContext, GLOBAL_OPTIONS, resolve_context
from mmffc.core.errors import MMFFCError
from mmffc.core.output import OutputFormat

F = TypeVar("F", bound=Callable[..., Any])


def _add_option(fn: F, *args: Any, **kwargs: Any) -> F:
    return click.option(*args, **kwargs)(fn)


def common_options(fn: F) -> F:
    """Attach the global option set to a command (CLI.md 2.2)."""
    for name in reversed(GLOBAL_OPTIONS):
        if name == "config_file":
            fn = _add_option(
                fn,
                "--config",
                "config_file",
                type=click.Path(exists=True, dir_okay=False),
                envvar="MMFFC_CONFIG",
                default=None,
                help="指定配置文件路径",
            )
        elif name == "output_format":
            fn = _add_option(
                fn,
                "--format",
                "output_format",
                type=click.Choice([item.value for item in OutputFormat]),
                default=None,
                help="输出格式: table|json|ndjson|csv|tsv|raw",
            )
        elif name == "json_flag":
            fn = _add_option(
                fn,
                "--json",
                "json_flag",
                is_flag=True,
                default=None,
                help="JSON 输出 (等价 --format json)",
            )
        elif name == "ndjson_flag":
            fn = _add_option(
                fn,
                "--ndjson",
                "ndjson_flag",
                is_flag=True,
                default=None,
                help="NDJSON 流式输出 (每行一个 JSON 对象)",
            )
        elif name == "no_color":
            fn = _add_option(
                fn,
                "--no-color",
                "no_color",
                is_flag=True,
                default=None,
                envvar="NO_COLOR",
                help="禁用彩色输出",
            )
        elif name == "quiet":
            fn = _add_option(
                fn,
                "--quiet",
                "-q",
                "quiet",
                is_flag=True,
                default=None,
                help="仅输出错误",
            )
        elif name == "verbose":
            fn = _add_option(
                fn,
                "--verbose",
                "-v",
                "verbose",
                count=True,
                help="增加 stderr 日志级别 (可重复)",
            )
        elif name == "dry_run":
            fn = _add_option(
                fn,
                "--dry-run",
                "dry_run",
                is_flag=True,
                default=None,
                help="只打印操作计划，不落盘",
            )
        elif name == "yes":
            fn = _add_option(
                fn,
                "--yes",
                "-y",
                "yes",
                is_flag=True,
                default=None,
                help="跳过交互确认 (非 TTY 下高风险操作必需)",
            )
        elif name == "cwd":
            fn = _add_option(
                fn,
                "--cwd",
                "cwd",
                type=click.Path(exists=True, file_okay=False),
                default=None,
                help="指定工作目录",
            )
        elif name == "log_level":
            fn = _add_option(
                fn,
                "--log-level",
                "log_level",
                type=click.Choice(["debug", "info", "warn", "error"]),
                default=None,
                help="日志级别",
            )
    return fn


def _invoke_callback(fn: Callable[..., Any], ctx: click.Context, kwargs: dict[str, Any]) -> Any:
    """Call a command callback, dropping global-option kwargs it does not declare."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(ctx, **kwargs)
    if any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    ):
        return fn(ctx, **kwargs)
    accepted = {
        key: value for key, value in kwargs.items() if key in signature.parameters
    }
    return fn(ctx, **accepted)


def mmffc_command(name: str | None = None, **kwargs: Any) -> Callable[[F], F]:
    """Decorator: click.command + global options + CliContext injection."""

    def decorator(fn: F) -> F:
        fn = common_options(fn)

        @click.command(name=name, **kwargs)
        @click.pass_context
        @functools.wraps(fn)
        def wrapper(ctx: click.Context, *args: Any, **kw: Any) -> Any:
            ctx.obj = resolve_context(ctx)
            return _invoke_callback(fn, ctx, kw)

        return wrapper  # type: ignore[return-value]

    return decorator


def mmffc_group(name: str | None = None, **kwargs: Any) -> Callable[[F], F]:
    """Decorator: click.group (MMFFCGroup) + global options + CliContext injection."""

    def decorator(fn: F) -> F:
        fn = common_options(fn)
        kwargs.setdefault("cls", MMFFCGroup)

        @click.group(name=name, **kwargs)
        @click.pass_context
        @functools.wraps(fn)
        def wrapper(ctx: click.Context, *args: Any, **kw: Any) -> Any:
            ctx.obj = resolve_context(ctx)
            return fn(ctx, *args, **kw)

        return wrapper  # type: ignore[return-value]

    return decorator


class MMFFCGroup(click.Group):
    """Group whose ``command()`` attaches global options and
    injects a freshly resolved CliContext into every subcommand."""

    def command(self, *args: Any, **kwargs: Any) -> Callable[[F], F]:
        group_command = super().command(*args, **kwargs)

        def decorator(fn: F) -> F:
            fn = common_options(fn)

            @click.pass_context
            @functools.wraps(fn)
            def wrapper(ctx: click.Context, *a: Any, **kw: Any) -> Any:
                # Re-resolve with the full context chain: subcommand
                # level options (e.g. `mmffc mod search --ndjson`)
                # must override group level ones.
                ctx.obj = resolve_context(ctx)
                return _invoke_callback(fn, ctx, kw)

            return group_command(wrapper)  # type: ignore[return-value]

        return decorator


@mmffc_group()
@click.version_option(__version__, "-V", "--version", prog_name="mmffc")
def cli(ctx, **_kwargs) -> None:
    """MMFFC - Minecraft Mod / FancyMenu / FTB / Control 自动化框架。

    确定性 CLI：接收 stdin/文件输入，校验后原子落盘；不调用 LLM。
    完整命令规范见 CLI.md。
    """


def _print_error(exc: MMFFCError) -> None:
    import click

    click.echo(f"mmffc: 错误: {exc.message}", err=True)


# ----------------------------------------------------------------------
# register command groups (imported after the framework symbols above
# to keep the import graph acyclic)
# ----------------------------------------------------------------------
from mmffc.cli import cache, config, mcp, mod, rcon, shell, structure, world  # noqa: E402

cli.add_command(mod.mod)
cli.add_command(config.config)
cli.add_command(structure.structure)
cli.add_command(world.world)
cli.add_command(rcon.rcon)
cli.add_command(cache.cache)
cli.add_command(shell.shell)
cli.add_command(mcp.mcp)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point returning a stable exit code (CLI.md 2.3)."""
    try:
        code = cli.main(args=argv, prog_name="mmffc", standalone_mode=False)
        return int(code or 0)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code or 1)
    except click.Abort:
        return 130
    except MMFFCError as exc:
        _print_error(exc)
        return int(exc.exit_code)
    except SystemExit as exc:
        # Commands may raise SystemExit(n) for contract exit codes.
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        click.echo(str(code), err=True)
        return 1
    except KeyboardInterrupt:
        click.echo("中断", err=True)
        return 130
    except BrokenPipeError:
        # Unix SIGPIPE semantics: die quietly.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except Exception:
            pass
        return 141
