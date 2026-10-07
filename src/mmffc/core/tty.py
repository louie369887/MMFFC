"""TTY detection and interactive confirmation helpers (CLI.md 2.1 rules 4-5)."""

from __future__ import annotations

import sys

from mmffc.core.errors import UsageError


def is_tty() -> bool:
    """True only when both stdin and stdout are interactive terminals."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def require_yes(yes: bool, action: str) -> None:
    """High-risk operations require --yes when not attached to a TTY.

    CLI.md acceptance: "非 TTY 下高风险操作未加 --yes 时退出码为 2".
    """
    if not yes and not is_tty():
        raise UsageError(
            f"高风险操作 '{action}' 在非 TTY 环境下必须显式使用 --yes 确认",
        )


def confirm(action: str, *, default: bool = False) -> bool:
    """Interactive yes/no confirmation (TTY only)."""
    import click

    hint = "Y/n" if default else "y/N"
    return click.confirm(f"{action} [{hint}]", default=default, abort=False)
