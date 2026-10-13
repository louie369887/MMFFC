"""崩溃报告与 JVM 致命错误解析（Phase 6，只读）。

支持两类证据：

* ``crash-reports/crash-<yyyy-MM-dd_HH.MM.SS>-<client|server>.txt``
  —— ``---- Minecraft Crash Report ----`` 头、``Description:`` 崩溃类型、
  异常类与堆栈顶部帧、``Caused by:`` 链、模组清单
* ``hs_err_pid<PID>.log`` —— JVM 级致命错误（无 crash-reports 时优先怀疑），
  提取问题摘要、信号、Problematic frame 与 JVM 信息
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CRASH_DIR_NAME = "crash-reports"
CRASH_NAME_RE = re.compile(
    r"^crash-(?P<date>\d{4}-\d{2}-\d{2})_(?P<h>\d{2})\.(?P<m>\d{2})\."
    r"(?P<s>\d{2})-(?P<kind>client|server)\.txt$"
)
HS_ERR_NAME_RE = re.compile(r"^hs_err_pid(\d+)\.log$")

CRASH_HEADER = "---- Minecraft Crash Report ----"
SYSTEM_DETAILS = "-- System Details --"
MAX_FRAMES = 12
MAX_MODS = 64


@dataclass
class CrashReport:
    """一份解析后的崩溃报告。"""

    path: Path | None
    name: str | None = None
    description: str | None = None
    exception: str | None = None
    frames: list[str] = field(default_factory=list)
    caused_by: list[str] = field(default_factory=list)
    mods: list[dict[str, str]] = field(default_factory=list)
    fabric_mods: int | None = None
    found: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "file": self.name,
            "description": self.description,
            "exception": self.exception,
            "frames": self.frames,
            "caused_by": self.caused_by,
            "mods": self.mods,
            "fabric_mods": self.fabric_mods,
            "error": self.error,
        }


@dataclass
class JvmCrash:
    """一份解析后的 hs_err_pid 记录。"""

    path: Path | None
    pid: int | None = None
    summary: list[str] = field(default_factory=list)
    signal: str | None = None
    problem_frame: str | None = None
    vm_version: str | None = None
    native_memory: str | None = None
    found: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "file": self.path.name if self.path else None,
            "pid": self.pid,
            "summary": self.summary,
            "signal": self.signal,
            "problem_frame": self.problem_frame,
            "vm_version": self.vm_version,
            "native_memory": self.native_memory,
            "error": self.error,
        }


def find_crash_reports(game_dir: Path) -> list[Path]:
    """按时间戳排序的崩溃报告列表（新的在后）。"""
    crash_dir = Path(game_dir) / CRASH_DIR_NAME
    if not crash_dir.is_dir():
        return []
    return sorted(
        path
        for path in crash_dir.glob("crash-*.txt")
        if path.is_file() and CRASH_NAME_RE.match(path.name)
    )


def find_hs_err_files(game_dir: Path) -> list[Path]:
    """JVM 致命错误文件（按修改时间排序）。"""
    root = Path(game_dir)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.glob("hs_err_pid*.log") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )


def _mod_name_from_filename(stem: str) -> str:
    """从文件名推断模组 id（截到第一段以数字开头的版本号）。"""
    parts = stem.split("-")
    kept: list[str] = []
    for part in parts:
        if re.match(r"^\d", part):
            break
        kept.append(part)
    return "-".join(kept) or stem


def parse_crash_report(text: str, path: Path | None = None) -> CrashReport:
    """解析崩溃报告文本。"""
    report = CrashReport(path=path)
    if CRASH_HEADER not in text:
        report.error = "缺少 Minecraft Crash Report 头"
        return report
    report.found = True
    if path is not None:
        report.name = path.name

    lines = text.splitlines()
    header_at = next(
        index for index, line in enumerate(lines) if CRASH_HEADER in line
    )
    header = lines[header_at : header_at + 24]
    for line in header:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip() == "Description":
            report.description = value.strip() or None

    detail_at = next(
        (index for index, line in enumerate(lines) if SYSTEM_DETAILS in line),
        len(lines),
    )
    body = lines[header_at + 1 : detail_at]

    stack_zone = False
    for raw in body:
        line = raw.rstrip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("-- "):
            continue
        if line.startswith("\t") or line.startswith("    at "):
            stack_zone = True
            if len(report.frames) < MAX_FRAMES:
                report.frames.append(line.strip())
            continue
        if line.startswith("Caused by:"):
            report.caused_by.append(line.strip())
            stack_zone = False
            continue
        if stack_zone:
            continue
        if line.startswith(("Time:", "Description:", "Mod List:")):
            continue
        if report.exception is None:
            report.exception = line.strip()
            stack_zone = True  # 下一批 at 行属于该异常

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "Mod List:" or re.match(r"^\s*Fabric Mods:", line):
            for mod_line in lines[index + 1 : index + 1 + MAX_MODS]:
                candidate = mod_line.strip()
                if not candidate:
                    break
                match = re.match(r"^([\w.\-]+?\.jar)\s+\|\s+(.+)$", candidate)
                if match:
                    report.mods.append(
                        {
                            "name": _mod_name_from_filename(match.group(1)[:-4]),
                            "file": match.group(1),
                            "version": match.group(2).strip(),
                        }
                    )
            break
    for line in lines:
        match = re.match(r"^\s*Fabric Mods:\s*(\d+)\s*$", line)
        if match:
            report.fabric_mods = int(match.group(1))
    return report


def parse_hs_err(text: str, path: Path | None = None) -> JvmCrash:
    """解析 JVM hs_err_pid 日志。"""
    crash = JvmCrash(path=path)
    if "# There is insufficient memory" not in text and "#  SIGSEGV" not in text and "jvm crash" not in text.lower() and "# A fatal error has been detected" not in text:
        crash.error = "未识别为 hs_err 文件"
        return crash
    crash.found = True
    if path is not None:
        match = HS_ERR_NAME_RE.match(path.name)
        if match:
            crash.pid = int(match.group(1))

    lines = text.splitlines()
    summary: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and len(summary) < 6:
            body = stripped.lstrip("#").strip()
            if body and not body.startswith("----"):
                summary.append(body)
    crash.summary = summary

    for index, line in enumerate(lines):
        stripped = line.strip()
        signal = re.search(r"#\s*(SIG[A-Z]+|EXCEPTION_[A-Z_]+)\s*\(", stripped)
        if signal and crash.signal is None:
            crash.signal = signal.group(1)
        if stripped.startswith("# Problematic frame:") and crash.problem_frame is None:
            value = stripped.split(":", 1)[1].strip()
            if not value:
                # 实际帧描述在紧随的注释行
                for follow in lines[index + 1 : index + 6]:
                    candidate = follow.strip().lstrip("#").strip()
                    if candidate:
                        value = candidate
                        break
            crash.problem_frame = value or None
        if stripped.startswith("# Java VM:") and crash.vm_version is None:
            crash.vm_version = stripped.split(":", 1)[1].strip()
        if "Native memory allocation" in stripped and crash.native_memory is None:
            crash.native_memory = stripped.lstrip("#").strip()
    return crash


def latest_crash(game_dir: Path, *, file: Path | None = None) -> CrashReport:
    """读取指定或最新的崩溃报告。"""
    if file is not None:
        path = Path(file)
        return parse_crash_report(path.read_text(encoding="utf-8", errors="replace"), path)
    reports = find_crash_reports(game_dir)
    if not reports:
        return CrashReport(path=None)
    path = reports[-1]
    return parse_crash_report(
        path.read_text(encoding="utf-8", errors="replace"), path
    )


def latest_jvm_crash(game_dir: Path) -> JvmCrash:
    """读取最新的 JVM 致命错误（没有则返回 found=False）。"""
    files = find_hs_err_files(game_dir)
    if not files:
        return JvmCrash(path=None)
    path = files[-1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        crash = JvmCrash(path=path)
        crash.error = f"读取失败: {exc}"
        return crash
    return parse_hs_err(text, path)
