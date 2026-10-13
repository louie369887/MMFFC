"""崩溃归因分类（Phase 6）。

把日志/崩溃证据映射为结构化原因码；匹配按 SIGNATURES 顺序短路
（先致命、后一般），命中即返回，附带证据行与修复建议。

原因码与 CLI.md 3.8 约定一致：

======  ==========================================
code    含义
======  ==========================================
java_version  Java 版本不匹配（UnsupportedClassVersionError，类文件版本 = Java 版本 + 44）
port_bind     端口绑定失败（**** FAILED TO BIND TO PORT!）
eula          未同意 EULA（服务端拒绝启动）
mod_load      模组/插件加载失败、缺失依赖
mixin         Mixin 应用失败
oom           内存不足（OutOfMemoryError / Java heap space）
world_corrupt 世界/区块损坏
launcher      启动器自身失败（launcher_log.txt）
unknown       未知（保留原始证据）
======  ==========================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

JAVA_VERSION = "java_version"
PORT_BIND = "port_bind"
EULA = "eula"
MOD_LOAD = "mod_load"
MIXIN = "mixin"
OOM = "oom"
WORLD_CORRUPT = "world_corrupt"
JVM_FATAL = "jvm_fatal"
LAUNCHER = "launcher"
UNKNOWN = "unknown"

TITLES = {
    JAVA_VERSION: "Java 版本不匹配",
    PORT_BIND: "端口绑定失败",
    EULA: "未同意 EULA",
    MOD_LOAD: "模组加载失败 / 依赖缺失",
    MIXIN: "Mixin 应用失败",
    OOM: "内存不足",
    JVM_FATAL: "JVM 致命错误（native 崩溃 / 内存映射失败）",
    WORLD_CORRUPT: "世界 / 区块损坏",
    LAUNCHER: "启动器失败",
    UNKNOWN: "未知原因",
}

SUGGESTIONS = {
    JAVA_VERSION: (
        "安装该 MC 版本要求的 Java（类文件版本 = Java 版本 + 44，"
        "例如 1.20.1 需要 Java 17；2026 年后的新版本需要 Java 21+），"
        "并用 mmffc game verify --java <path> 指定 JVM"
    ),
    PORT_BIND: (
        "关闭占用该端口的进程，或在 server.properties 中修改 "
        "server-port（mmffc game verify 默认使用随机空闲端口）"
    ),
    EULA: "在服务端目录放置 eula.txt 并写入 eula=true（需先同意 Mojang EULA）",
    MOD_LOAD: (
        "补齐或移除报错的模组：按堆栈中的 mod-id 去 Modrinth/CurseForge "
        "确认版本兼容性，可用 mmffc mod deps --missing 检查依赖"
    ),
    MIXIN: "更新 Mixin 冲突的模组到兼容版本，或用排除法逐个停用模组定位",
    OOM: "增大 JVM 内存（-Xmx，默认 1024M 可能不够大型整合包）",
    JVM_FATAL: (
        "查看 hs_err_pid 文件的 Problematic frame；常见于显卡驱动/"
        "原生模组（如 Sodium）与内存映射失败，更新驱动与模组或降低内存压力"
    ),
    WORLD_CORRUPT: "用 mmffc world validate 检查存档，必要时从备份恢复区块",
    LAUNCHER: "见 launcher_log.txt；多为启动器自身更新或 Java 路径问题",
    UNKNOWN: "保留 evidence 原文提交 issue，或用 mclogs 分享完整 latest.log",
}

# (code, 正则模式) —— 顺序即优先级
SIGNATURES: tuple[tuple[str, str], ...] = (
    (JAVA_VERSION, r"UnsupportedClassVersionError|Unsupported major\.minor version"),
    (OOM, r"OutOfMemoryError|Java heap space|insufficient memory"),
    (EULA, r"You need to agree to the EULA|Mojang EULA"),
    (MIXIN, r"Mixin (?:apply|transformer).*failed|MixinTransformerError|mixin apply failed"),
    (PORT_BIND, r"FAILED TO BIND TO PORT|BindException|Address already in use"),
    (
        MOD_LOAD,
        r"Could not load|Unknown/missing dependency|UnknownDependencyException"
        r"|Missing or unsupported mandatory dependencies|ModResolutionException"
        r"|Failed to create mod instance|Duplicate mod|Mod file .* is missing",
    ),
    (
        WORLD_CORRUPT,
        r"Failed to read chunk|Corrupt chunk|Chunk file .* is corrupt"
        r"|Region file .* corrupt|Failed to load level data",
    ),
    (JVM_FATAL, r"# A fatal error has been detected|SIGSEGV|EXCEPTION_ACCESS_VIOLATION"),
)


@dataclass
class Signature:
    """一条命中的归因记录。"""

    code: str
    title: str
    suggestion: str
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "attribution": self.code,
            "attribution_title": self.title,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
        }


def _scan_lines(lines: list[tuple[str, int, str]]) -> tuple[str, str, int]:
    """按 SIGNATURES 顺序找出第一处命中（返回 (code, 行, 行号)）。"""
    for code, pattern in SIGNATURES:
        compiled = re.compile(pattern)
        for source, lineno, line in lines:
            if compiled.search(line):
                return (code, line, lineno)
    return (UNKNOWN, "", -1)


def classify(
    log_lines: list[tuple[str, int, str]],
    *,
    crash_lines: list[tuple[str, int, str]] | None = None,
    jvm_text: str | None = None,
) -> Signature:
    """综合日志、崩溃报告与 JVM 错误给出归因。

    ``log_lines`` / ``crash_lines`` 为 ``(来源, 行号, 文本)`` 三元组。
    优先级：崩溃报告 > 日志 > JVM 错误（报告里通常已含根因）；
    同池内按 SIGNATURES 顺序短路（先致命、后一般）。
    """
    pools: list[list[tuple[str, int, str]]] = []
    if crash_lines:
        pools.append(crash_lines)
    if log_lines:
        pools.append(log_lines)
    if jvm_text:
        pools.append(
            [
                ("hs_err", index, line)
                for index, line in enumerate(jvm_text.splitlines(), start=1)
            ]
        )

    code = UNKNOWN
    for pool in pools:
        found, _, _ = _scan_lines(pool)
        if found != UNKNOWN:
            code = found
            break

    return Signature(
        code=code,
        title=TITLES[code],
        suggestion=SUGGESTIONS[code],
        evidence=_collect(code, log_lines, crash_lines, jvm_text),
    )


def _collect(
    code: str,
    log_lines: list[tuple[str, int, str]],
    crash_lines: list[tuple[str, int, str]] | None,
    jvm_text: str | None,
) -> list[dict]:
    """收集同一原因码的所有证据（上限若干条，按来源稳定排序）。"""
    if code == UNKNOWN:
        return []
    pattern = dict(SIGNATURES)[code]
    compiled = re.compile(pattern)
    evidence: list[dict] = []
    seen: set[str] = set()
    pools: list[tuple[str, list[tuple[str, int, str]]]] = [
        ("crash-report", crash_lines or []),
        ("latest.log", log_lines),
    ]
    if jvm_text:
        pools.append(
            (
                "hs_err",
                [
                    ("hs_err", index, line)
                    for index, line in enumerate(
                        jvm_text.splitlines(), start=1
                    )
                ],
            )
        )
    for source, pool in pools:
        for entry_source, lineno, line in pool:
            key = line.strip()
            if not key or key in seen:
                continue
            if compiled.search(line):
                seen.add(key)
                evidence.append(
                    {"source": source, "lineno": lineno, "line": line.strip()}
                )
            if len(evidence) >= 8:
                return evidence
    return evidence


def severity_for(code: str) -> str:
    """归因码 -> 严重级别（info / error / fatal）。"""
    if code == UNKNOWN:
        return "info"
    if code in (OOM, WORLD_CORRUPT, JAVA_VERSION, JVM_FATAL):
        return "fatal"
    return "error"
