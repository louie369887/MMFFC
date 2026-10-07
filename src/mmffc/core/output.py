"""Output rendering: table / json / ndjson / csv / tsv / raw (CLI.md 2.2)."""

from __future__ import annotations

import csv
import io
import json
from enum import Enum
from typing import Any, Iterable, Sequence

import click
from rich.console import Console
from rich.table import Table


class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"
    NDJSON = "ndjson"
    CSV = "csv"
    TSV = "tsv"
    RAW = "raw"


def _json_default(obj: Any) -> Any:
    return str(obj)


def format_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=_json_default)


def format_ndjson(items: Iterable[Any]) -> str:
    lines = [json.dumps(item, ensure_ascii=False, default=_json_default) for item in items]
    return "".join(line + "\n" for line in lines)


def _as_rows(data: Any) -> tuple[list[str], list[dict]] | None:
    """Normalize common payloads into (columns, rows) for tabular output."""
    if isinstance(data, list):
        if not data:
            return [], []
        if all(isinstance(row, dict) for row in data):
            columns: list[str] = []
            for row in data:
                for key in row:
                    if key not in columns:
                        columns.append(key)
            return columns, data
        if all(isinstance(row, (str, int, float, bool)) or row is None for row in data):
            return ["value"], [{"value": row} for row in data]
        return None
    if isinstance(data, dict):
        if all(
            isinstance(value, (str, int, float, bool)) or value is None
            for value in data.values()
        ):
            return list(data.keys()), [data]
        return None
    return None


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    return str(value)


def _emit_csv(data: Any, delimiter: str) -> None:
    rows = _as_rows(data)
    if rows is None:
        click.echo(format_json(data))
        return
    columns, records = rows
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(columns)
    for record in records:
        writer.writerow([_format_cell(record.get(column)) for column in columns])
    click.echo(buffer.getvalue(), nl=False)


def _emit_table(data: Any, console: Console) -> None:
    rows = _as_rows(data)
    if rows is None:
        console.print_json(format_json(data))
        return
    columns, records = rows
    if not records:
        return
    table = Table(show_header=True, header_style="bold cyan", box=None)
    for column in columns:
        table.add_column(column, max_width=48, overflow="fold", no_wrap=False)
    for record in records:
        table.add_row(*[_format_cell(record.get(column)) for column in columns])
    console.print(table)


class OutputRenderer:
    """Renders command results according to the resolved output format."""

    def __init__(self, fmt: OutputFormat = OutputFormat.TABLE, *, color: bool = True) -> None:
        self.fmt = fmt
        self.console = Console(no_color=not color, force_terminal=None)

    def emit(self, data: Any) -> None:
        """Write a result payload to stdout."""
        if self.fmt is OutputFormat.JSON:
            click.echo(format_json(data))
        elif self.fmt is OutputFormat.NDJSON:
            if isinstance(data, (list, tuple)):
                click.echo(format_ndjson(data), nl=False)
            else:
                click.echo(format_ndjson([data]), nl=False)
        elif self.fmt is OutputFormat.CSV:
            _emit_csv(data, ",")
        elif self.fmt is OutputFormat.TSV:
            _emit_csv(data, "\t")
        elif self.fmt is OutputFormat.RAW:
            if isinstance(data, str):
                click.echo(data, nl=False)
            else:
                click.echo(format_json(data))
        else:
            _emit_table(data, self.console)

    def info(self, message: str) -> None:
        """Human-readable notice on stdout (table/raw modes only)."""
        if self.fmt in (OutputFormat.TABLE, OutputFormat.RAW):
            self.console.print(f"[dim]{message}[/dim]")
