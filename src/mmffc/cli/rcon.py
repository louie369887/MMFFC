"""mmffc rcon — 游戏内热重载桥接。

通过 RCON 协议触发游戏内配置重载
（FancyMenu reload / FTB Quests reload）。

安全提醒：RCON 明文传输，不应通过公网暴露，
建议仅限本地连接或使用 SSH 隧道。
单人游戏（无 RCON）使用文件监听方案
（``mmffc rcon watch``）。
"""

from __future__ import annotations

import os

import click

from mmffc.cli import mmffc_group
from mmffc.core.context import CliContext
from mmffc.net.rcon import RconClient, reload_commands


@mmffc_group()
def rcon(ctx, **_kwargs) -> None:
    """游戏内热重载（Phase 2）。"""


def _password(ctx: click.Context, password_env: str) -> str:
    settings: CliContext = ctx.obj
    password = os.environ.get(password_env)
    if not password:
        password = settings.config.data.get("rcon_password")
    if not password:
        raise click.UsageError(
            f"RCON 密码未设置：请设置环境变量 {password_env} "
            f"或在配置文件中写入 rcon_password"
        )
    return password


@rcon.command("exec")
@click.argument("command")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=25575, show_default=True)
@click.option("--password-env", "password_env", default="MMFFC_RCON_PASSWORD", show_default=True)
def rcon_exec(ctx, command, host, port, password_env):
    """通过 RCON 执行游戏内命令。"""
    password = _password(ctx, password_env)
    with RconClient(host, port, password) as client:
        output = client.execute(command)
    click.echo(output, nl=False)


@rcon.command("reload")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests", "all"]), default="all")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=25575, show_default=True)
@click.option("--password-env", "password_env", default="MMFFC_RCON_PASSWORD", show_default=True)
def rcon_reload(ctx, target, host, port, password_env):
    """触发游戏内配置重载。"""
    settings: CliContext = ctx.obj
    password = _password(ctx, password_env)

    commands = reload_commands(target)
    results = []
    with RconClient(host, port, password) as client:
        for command in commands:
            output = client.execute(command)
            results.append({"command": command, "output": output.strip()})

    settings.renderer.emit({"reloaded": results, "count": len(results)})


@rcon.command("watch")
@click.option("--dest", "dest_dir", type=click.Path(file_okay=False), required=True, help="监听的配置目录")
@click.option("--recursive", is_flag=True, default=True, help="递归监听子目录")
def rcon_watch(ctx, dest_dir, recursive):
    """单人游戏方案：监听配置目录变化，提示手动 /reload。"""
    settings: CliContext = ctx.obj
    dest = dest_dir if isinstance(dest_dir, str) else str(dest_dir)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        raise click.ClickException(
            "文件监听需要 watchdog：pip install watchdog"
        ) from None

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            settings.renderer.console.print(
                f"[yellow]配置已变更: {event.src_path}[/yellow]"
            )
            settings.renderer.console.print(
                "[dim]请在游戏内执行 /reload[/dim]"
            )

    observer = Observer()
    observer.schedule(_Handler(), dest, recursive=recursive)
    observer.start()
    settings.renderer.console.print(
        f"[cyan]监听中: {dest} (Ctrl+C 退出)[/cyan]"
    )
    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
