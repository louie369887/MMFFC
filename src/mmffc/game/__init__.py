"""Phase 6 模块：日志解析、崩溃报告解析、归因分类、版本保活验证。"""

from mmffc.game.attribution import TITLES, classify, severity_for
from mmffc.game.crash import (
    CrashReport,
    JvmCrash,
    find_crash_reports,
    find_hs_err_files,
    latest_crash,
    latest_jvm_crash,
    parse_crash_report,
    parse_hs_err,
)
from mmffc.game.logs import (
    LEVEL_CHOICES,
    LogEntry,
    LogScan,
    discover_logs,
    parse_log_text,
    read_log_bytes,
    scan_source,
    select_entries,
)
from mmffc.game.verify import (
    LOADERS,
    VerifyError,
    VerifyResult,
    build_plan,
    find_server_jar,
    free_port,
    verify_version,
)

__all__ = [
    "LOADERS",
    "LEVEL_CHOICES",
    "TITLES",
    "CrashReport",
    "JvmCrash",
    "LogEntry",
    "LogScan",
    "VerifyError",
    "VerifyResult",
    "build_plan",
    "classify",
    "discover_logs",
    "find_crash_reports",
    "find_hs_err_files",
    "find_server_jar",
    "free_port",
    "latest_crash",
    "latest_jvm_crash",
    "parse_crash_report",
    "parse_hs_err",
    "parse_log_text",
    "read_log_bytes",
    "scan_source",
    "select_entries",
    "severity_for",
    "verify_version",
]
