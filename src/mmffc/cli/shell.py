"""mmffc shell — 可选交互式 REPL（DOS 风格交互层）。

统一入口 ``mmffc``（无参数）进入同一 REPL；本命令保留是为了
显式调用与脚本兼容。Unix 管道自动化不经过这里。
"""

from __future__ import annotations

from mmffc.cli import mmffc_command


@mmffc_command()
def shell(ctx, **_kwargs):
    """交互式 shell（mmffc> 提示符；自动化请使用管道）。"""
    from mmffc.launcher import run_shell

    raise SystemExit(run_shell())
