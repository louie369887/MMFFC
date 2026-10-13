"""游戏日志发现与解析（Phase 6，只读）。

证据文件位置（确定性路径，见 CLI.md 3.8）::

    <gameDir>/logs/latest.log            # 本次运行全量日志
    <gameDir>/logs/debug.log             # verbose 版
    <gameDir>/logs/<yyyy-MM-dd>-<n>.log.gz   # 历史运行（gzip）
    <gameDir>/crash-reports/crash-*.txt  # 崩溃报告（见 game.crash）
    <gameDir>/hs_err_pid<PID>.log        # JVM 级致命错误

行格式（vanilla 与 modded 兼容）::

    [HH:mm:ss] [Thread/LEVEL]: message
    [HH:mm:ss] [Thread/LEVEL] (ModID): message

解析器对无法匹配的行保留原文（level=None），保证证据不丢失。
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path

LOG_DIR_NAME = "logs"
LATEST_NAME = "latest.log"
DEBUG_NAME = "debug.log"
HISTORY_PATTERN = "*.log.gz"

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "FATAL": 50}
LEVEL_CHOICES = ("debug", "info", "warn", "error", "fatal")

LOG_LINE_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]\s+"
    r"(?:\((?P<tag>[^)]*)\)\s*)?"
    r"\[(?P<source>[^\]]*)/(?P<level>[A-Z]+)\]\s*"
    r"(?:\[(?P<logger>[^\]]*)\]\s*)?"
    r"(?::\s*)?"
    r"(?P<message>.*)$"
)

EXCEPTION_HINTS = (
    "Exception",
    "Error",
    "Throwable",
    "Caused by",
    "at ",
)


@dataclass(frozen=True)
class LogEntry:
    """一条日志记录（无法解析的行 level 为 None，message 保留原文）。"""

    lineno: int
    message: str
    time: str | None = None
    source: str | None = None
    level: str | None = None
    tag: str | None = None
    logger: str | None = None

    @property
    def severity(self) -> int:
        return LEVELS.get(self.level or "", 0)

    @property
    def is_problem(self) -> bool:
        return self.level in ("ERROR", "FATAL")

    def to_dict(self) -> dict:
        return {
            "lineno": self.lineno,
            "time": self.time,
            "source": self.source,
            "level": self.level,
            "tag": self.tag,
            "message": self.message,
        }


@dataclass
class LogScan:
    """一次日志扫描的结果。"""

    path: Path | None
    entries: list[LogEntry] = field(default_factory=list)

    @property
    def errors(self) -> list[LogEntry]:
        return [entry for entry in self.entries if entry.is_problem]

    @property
    def warnings(self) -> list[LogEntry]:
        return [
            entry
            for entry in self.entries
            if entry.level in ("WARN", "ERROR", "FATAL")
        ]


def parse_log_text(text: str, *, path: Path | None = None) -> LogScan:
    """解析日志文本为结构化记录。"""
    scan = LogScan(path=path)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        match = LOG_LINE_RE.match(line)
        if match is None:
            scan.entries.append(LogEntry(lineno=lineno, message=line))
            continue
        scan.entries.append(
            LogEntry(
                lineno=lineno,
                message=match.group("message") or "",
                time=match.group("time"),
                source=match.group("source"),
                level=match.group("level").upper(),
                tag=match.group("tag"),
                logger=match.group("logger"),
            )
        )
    return scan


def read_log_bytes(raw: bytes, path: Path) -> str:
    """读取日志字节（自动处理 .gz），损坏时容错返回可解码部分。"""
    data = raw
    if path.suffix == ".gz":
        try:
            data = gzip.decompress(raw)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise ValueError(f"gzip 解压失败: {exc}") from exc
    return data.decode("utf-8", errors="replace")


def discover_logs(game_dir: Path) -> dict[str, list[Path]]:
    """定位 latest / debug / history 日志（不存在则为空列表）。"""
    log_dir = Path(game_dir) / LOG_DIR_NAME
    if not log_dir.is_dir():
        return {"latest": [], "debug": [], "history": []}
    return {
        "latest": [log_dir / LATEST_NAME]
        if (log_dir / LATEST_NAME).is_file()
        else [],
        "debug": [log_dir / DEBUG_NAME]
        if (log_dir / DEBUG_NAME).is_file()
        else [],
        "history": sorted(
            path for path in log_dir.glob(HISTORY_PATTERN) if path.is_file()
        ),
    }


def scan_source(game_dir: Path, source: str) -> list[LogScan]:
    """按 latest|debug|history|all 扫描日志文件。"""
    found = discover_logs(game_dir)
    paths: list[Path] = []
    if source == "all":
        for key in ("latest", "debug", "history"):
            paths.extend(found[key])
    else:
        paths = found.get(source, [])
    scans: list[LogScan] = []
    for path in paths:
        try:
            text = read_log_bytes(path.read_bytes(), path)
        except (OSError, ValueError) as exc:
            scans.append(LogScan(path=path))
            scans[-1].entries.append(
                LogEntry(lineno=0, message=f"读取失败: {exc}")
            )
            continue
        scans.append(parse_log_text(text, path=path))
    return scans


def select_entries(
    scans: list[LogScan],
    *,
    min_level: str = "info",
    tail: int | None = None,
) -> list[dict]:
    """按严重级别过滤并截取尾部，输出 JSON 友好的记录列表。"""
    threshold = LEVELS.get(min_level.upper(), 20)
    entries: list[LogEntry] = []
    for scan in scans:
        entries.extend(
            entry for entry in scan.entries if entry.severity >= threshold
        )
    if tail is not None and tail > 0:
        entries = entries[-tail:]
    return [entry.to_dict() for entry in entries]
