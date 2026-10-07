"""mmffc mcp — MCP Server 生命周期管理。

MCP Server 作为独立进程运行，通过 stdio / SSE /
Streamable HTTP 与外部 AI 通信。
"""

from __future__ import annotations

import click

from mmffc.cli import mmffc_command
from mmffc.core.context import CliContext


@mmffc_command()
@click.option("--transport", type=click.Choice(["stdio", "sse", "streamable-http"]), default="stdio", show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True, help="SSE / HTTP 传输的监听地址")
@click.option("--port", type=int, default=8080, show_default=True, help="SSE / HTTP 传输的监听端口")
def mcp(ctx, transport, host, port):
    """启动 MMFFC MCP Server（AI 高阶模块入口）。

    stdio 模式供 Claude Desktop / Cursor 等客户端
    以子进程方式调用；stdout 仅承载 MCP 协议，
    日志输出到 stderr。
    """
    settings: CliContext = ctx.obj

    from mmffc.mcp.server import run

    if transport == "stdio":
        run("stdio")
        return

    if transport == "sse":
        import uvicorn

        app = _sse_app()
        uvicorn.run(app, host=host, port=port, log_level="info")
        return

    raise click.ClickException(
        "streamable-http 传输请使用 mmffc.mcp.server.run()"
    )


def _sse_app():
    from mmffc.mcp.server import server

    return server.sse_app()
