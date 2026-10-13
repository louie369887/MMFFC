"""统一入口 launcher：横幅、提示符 shell、模式分发、exe 契约。"""

from __future__ import annotations

import json

import pytest

from mmffc.launcher import (
    BANNER_WORD,
    PROMPT,
    build_banner,
    main,
    run_shell,
)
from mmffc import __version__


def test_banner_is_3d_extruded():
    banner = build_banner()
    lines = banner.split("\n")
    assert len(lines) == 6
    assert BANNER_WORD in BANNER_WORD  # 保持常量引用
    assert "█" in banner
    assert "▒" in banner  # 挤出阴影层
    # 矩形画布：所有行等宽
    assert len({len(line) for line in lines}) == 1
    # 首行是纯字面（无阴影），末行几乎全是阴影
    assert "▒" not in lines[0]
    assert "▒" in lines[-1]


def test_banner_word_characters_only():
    banner = build_banner("ABC")
    assert "█" in banner


def test_banner_ignores_unknown_characters():
    assert build_banner("M1") == build_banner("M")


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--version"], "0.1.0"),
        (["game", "--help"], "崩溃诊断"),
        (["world", "--help"], "MCA 编译与世界生成"),
    ],
)
def test_dispatch_one_shot(argv, expected, capsys):
    code = main(argv)
    assert code == 0
    assert expected in capsys.readouterr().out


def test_dispatch_returns_exit_code(capsys):
    # 不存在的数据目录 -> world inspect 退出码 2
    code = main(["world", "inspect", "definitely-not-a-world"])
    assert code == 2
    capsys.readouterr()


def test_dispatch_unknown_command(capsys):
    assert main(["definitely-not-a-command"]) == 2


def test_mcp_flag_recognized(monkeypatch):
    called = []
    monkeypatch.setattr(
        "mmffc.launcher._run_mcp", lambda: called.append(1) or 0
    )
    assert main(["--mcp"]) == 0
    assert called == [1]


def test_no_args_non_tty_prints_banner(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "█" in out
    assert "无交互输入" in out
    assert PROMPT not in out


def test_shell_exit_command(monkeypatch, capsys):
    """shell 内直接输入 exit -> 干净退出。"""
    import builtins

    inputs = iter(["exit"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert run_shell() == 0


def test_shell_runs_command_without_prefix(monkeypatch, capsys):
    """shell 内 `world --help`（省略 mmffc 前缀）得到命令树。"""
    import builtins

    inputs = iter(["world --help", "exit"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert run_shell() == 0
    out = capsys.readouterr().out
    assert "MCA 编译与世界生成" in out
    assert __version__ in out
