"""Per-invocation CLI settings shared by all commands.

Global options may be given before the noun (``mmffc --json mod search``) or
after it (``mmffc mod search --ndjson``); values from the nearest context win.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

import click

from mmffc.core.config import MMFFCConfig, load_config
from mmffc.core.errors import MMFFCError
from mmffc.core.output import OutputFormat, OutputRenderer

GLOBAL_OPTIONS = (
    "config_file",
    "output_format",
    "json_flag",
    "ndjson_flag",
    "no_color",
    "quiet",
    "verbose",
    "dry_run",
    "yes",
    "cwd",
    "log_level",
)


@dataclass
class CliContext:
    """Resolved runtime context passed to every command via ctx.obj."""

    config: MMFFCConfig
    fmt: OutputFormat
    renderer: OutputRenderer
    dry_run: bool = False
    yes: bool = False
    quiet: bool = False
    verbose: int = 0
    cwd: str | None = None
    log_level: str = "warn"


def _walk_params(ctx: click.Context) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current: click.Context | None = ctx
    while current is not None:
        chain.append(current.params)
        current = current.parent
    return chain


def resolve_context(ctx: click.Context) -> CliContext:
    """Merge global options from the whole context chain (leaf wins)."""
    chain = _walk_params(ctx)

    def pick(key: str) -> Any:
        for params in chain:
            value = params.get(key)
            if value is not None:
                return value
        return None

    verbose = sum(int(params.get("verbose") or 0) for params in chain)

    json_flag = pick("json_flag")
    ndjson_flag = pick("ndjson_flag")
    if json_flag and ndjson_flag:
        raise click.UsageError("--json 与 --ndjson 互斥")

    fmt_name = pick("output_format") or ("ndjson" if ndjson_flag else "json" if json_flag else "table")
    fmt = OutputFormat(fmt_name)

    no_color = bool(pick("no_color") or os.environ.get("NO_COLOR"))
    quiet = bool(pick("quiet"))
    dry_run = bool(pick("dry_run"))
    yes = bool(pick("yes"))

    if verbose >= 2:
        log_level = "debug"
    elif verbose == 1:
        log_level = "info"
    elif quiet:
        log_level = "error"
    else:
        log_level = "warn"
    log_level = pick("log_level") or log_level

    level = getattr(logging, log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    cwd = pick("cwd")
    if cwd:
        os.chdir(cwd)

    config_file = pick("config_file")
    try:
        config = load_config(config_file)
    except MMFFCError as exc:
        raise click.UsageError(exc.message) from exc

    renderer = OutputRenderer(fmt=fmt, color=not no_color)
    return CliContext(
        config=config,
        fmt=fmt,
        renderer=renderer,
        dry_run=dry_run,
        yes=yes,
        quiet=quiet,
        verbose=verbose,
        cwd=cwd,
        log_level=log_level,
    )
