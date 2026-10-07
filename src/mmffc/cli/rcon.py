"""mmffc rcon — 游戏内热重载桥接 (Phase 2 交付)。

RCON 协议客户端将在 Phase 2 实现（mcrcon / mcnexus 或原生 socket）。
"""

from __future__ import annotations

import click

from mmffc.cli import mmffc_command, mmffc_group


@mmffc_group()
def rcon(ctx, **_kwargs) -> None:
    """游戏内热重载 (Phase 2)。"""


@rcon.command("exec")
@click.argument("command")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=25575, show_default=True)
@click.option("--password-env", "password_env", default="MMFFC_RCON_PASSWORD", show_default=True)
def rcon_exec(ctx, command, host, port, password_env):
    """通过 RCON 执行游戏内命令 (Phase 2 实现)。"""
    raise click.ClickException("rcon exec 将在 Phase 2 实现")


@rcon.command("reload")
@click.argument("target", type=click.Choice(["fancymenu", "ftbquests", "all"]), default="all")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=25575, show_default=True)
def rcon_reload(ctx, target, host, port):
    """触发配置热重载 (Phase 2 实现)。"""
    raise click.ClickException("rcon reload 将在 Phase 2 实现")
