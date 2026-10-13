"""Phase 6 game log/crash/diagnose 测试（只读诊断）。"""

from __future__ import annotations

import gzip
import json

import pytest

from mmffc.cli import main
from mmffc.game.attribution import classify, severity_for
from mmffc.game.crash import (
    latest_crash,
    latest_jvm_crash,
    parse_crash_report,
    parse_hs_err,
)
from mmffc.game.logs import parse_log_text, scan_source, select_entries

CRASH_TEXT = """---- Minecraft Crash Report ----
// I bet the enderman took it.

Time: 2026-10-11 14:35:22
Description: Ticking entity

java.lang.NullPointerException
\tat net.minecraft.world.World.func_70104_b(World.java:1042)
\tat create.foundation.utility.ServerSpeed.func_73660_a(ServerSpeed.java:37)
\tat net.minecraft.server.MinecraftServer.func_71217_p(MinecraftServer.java:700)

-- System Details --
Details:
\tMinecraft Version: 1.20.1
\tMod List:
\t\tcreate-1.20.1-0.5.1.jar | Create 0.5.1
\t\tforge-1.20.1-47.1.0-server.jar | Forge 47.1.0
"""

HS_ERR_TEXT = """#
# A fatal error has been detected by the Java Runtime Environment:
#
#  SIGSEGV (0xb) at pc=0x00007ffd4d5c2f10, pid=12345, tid=0x00000001
#
# JRE version: OpenJDK Runtime Environment (17.0.9+9)
# Java VM: OpenJDK 64-Bit Server VM (17.0.9+9, mixed mode)
#
# Problematic frame:
# C  [nvoglv64.dll+0x2f10]
"""

LOG_TEXT = """[14:35:20] [main/INFO]: Loading Minecraft 1.20.1
[14:35:21] [modloading-worker-2/INFO]: sodium loaded
[14:35:22] [Server thread/WARN]: Can't keep up! Did the system time change?
[14:35:23] [Server thread/ERROR]: Could not load 'plugins/broken.jar'
org.bukkit.plugin.UnknownDependencyException: Unknown/missing dependency
[14:35:24] [Server thread/FATAL]: UnsupportedClassVersionError: Bad version
"""


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "instance"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "latest.log").write_text(LOG_TEXT, encoding="utf-8")
    (root / "logs" / "debug.log").write_text(LOG_TEXT, encoding="utf-8")
    history = root / "logs" / "2026-10-10-1.log.gz"
    history.write_bytes(gzip.compress(LOG_TEXT.encode("utf-8")))
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ----------------------------------------------------------------------
# 日志解析
# ----------------------------------------------------------------------
def test_parse_log_entries():
    scan = parse_log_text(LOG_TEXT)
    levels = {entry.level for entry in scan.entries}
    # 堆栈续行不匹配日志格式，保留原文（level=None）
    assert levels == {"INFO", "WARN", "ERROR", "FATAL", None}
    assert len(scan.entries) == 6
    assert scan.entries[0].source == "main"
    assert scan.entries[0].message == "Loading Minecraft 1.20.1"
    assert scan.entries[2].time == "14:35:22"
    assert len(scan.errors) == 2
    assert len(scan.warnings) == 3


def test_parse_log_keeps_unparseable_lines():
    scan = parse_log_text("plain line\n[bad\n")
    assert scan.entries[0].level is None
    assert scan.entries[0].message == "plain line"


def test_select_entries_filters_level_and_tail():
    scan = parse_log_text(LOG_TEXT)
    errors = select_entries([scan], min_level="error")
    assert [entry["level"] for entry in errors] == ["ERROR", "FATAL"]
    tail = select_entries([scan], min_level="debug", tail=2)
    assert len(tail) == 2
    assert tail[-1]["level"] == "FATAL"


def test_scan_source_variants(instance):
    assert len(scan_source(instance, "latest")[0].entries) == 6
    assert len(scan_source(instance, "debug")[0].entries) == 6
    history = scan_source(instance, "history")
    assert history and history[0].path.suffix == ".gz"
    assert len(history[0].entries) == 6
    assert len(scan_source(instance, "all")) == 3


def test_scan_source_missing_dir(tmp_path):
    assert scan_source(tmp_path / "nope", "all") == []


# ----------------------------------------------------------------------
# 崩溃报告
# ----------------------------------------------------------------------
def test_parse_crash_report_fields():
    report = parse_crash_report(CRASH_TEXT)
    assert report.found is True
    assert report.description == "Ticking entity"
    assert report.exception == "java.lang.NullPointerException"
    assert any("World.java" in frame for frame in report.frames)
    assert {mod["name"] for mod in report.mods} == {"create", "forge"}
    assert report.mods[0]["file"] == "create-1.20.1-0.5.1.jar"
    assert "0.5.1" in report.mods[0]["version"]


def test_parse_crash_report_rejects_non_crash():
    report = parse_crash_report("just a log\n")
    assert report.found is False
    assert report.error


def test_latest_crash_picks_newest(instance):
    crash_dir = instance / "crash-reports"
    crash_dir.mkdir()
    (crash_dir / "crash-2026-01-01_10.00.00-client.txt").write_text(
        CRASH_TEXT.replace("Ticking entity", "Older"), encoding="utf-8"
    )
    (crash_dir / "crash-2026-02-02_11.11.11-server.txt").write_text(
        CRASH_TEXT.replace("Ticking entity", "Newer"), encoding="utf-8"
    )
    report = latest_crash(instance)
    assert report.description == "Newer"
    assert report.name == "crash-2026-02-02_11.11.11-server.txt"


def test_parse_hs_err_fields():
    crash = parse_hs_err(HS_ERR_TEXT)
    assert crash.found is True
    assert crash.signal == "SIGSEGV"
    assert crash.problem_frame is not None
    assert "nvoglv64" in crash.problem_frame


def test_latest_jvm_crash(instance):
    assert latest_jvm_crash(instance).found is False
    (instance / "hs_err_pid1234.log").write_text(HS_ERR_TEXT, encoding="utf-8")
    crash = latest_jvm_crash(instance)
    assert crash.found is True and crash.pid == 1234


# ----------------------------------------------------------------------
# 归因
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,code",
    [
        ("java.lang.UnsupportedClassVersionError: class 69.0", "java_version"),
        ("**** FAILED TO BIND TO PORT! ****", "port_bind"),
        ("Could not load 'plugins/x.jar'", "mod_load"),
        ("java.lang.OutOfMemoryError: Java heap space", "oom"),
        ("Mixin apply failed", "mixin"),
        ("You need to agree to the EULA", "eula"),
        ("#  SIGSEGV (0xb) at pc=0x0", "jvm_fatal"),
        ("nothing recognizable here", "unknown"),
    ],
)
def test_classify_signatures(line, code):
    signature = classify([("latest.log", 1, line)])
    assert signature.code == code
    assert signature.title


def test_classify_prefers_crash_report_over_log():
    signature = classify(
        [("latest.log", 5, "java.lang.OutOfMemoryError")],
        crash_lines=[("crash-report", 9, "Could not load 'mods/foo.jar'")],
    )
    assert signature.code == "mod_load"


def test_classify_collects_evidence():
    signature = classify(
        [("latest.log", 1, "Could not load 'a.jar'"),
         ("latest.log", 2, "warn: unrelated"),
         ("latest.log", 3, "Could not load 'b.jar'")]
    )
    assert len(signature.evidence) == 2
    assert all(item["source"] == "latest.log" for item in signature.evidence)


def test_severity_for():
    assert severity_for("java_version") == "fatal"
    assert severity_for("mod_load") == "error"
    assert severity_for("unknown") == "info"


# ----------------------------------------------------------------------
# CLI：game log / crash / diagnose / list
# ----------------------------------------------------------------------
def test_cli_log_json(instance, home, capsys):
    assert main(["game", "log", str(instance), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    # 堆栈续行 level=None 被 --level info 过滤，剩 5 条
    assert data["entry_count"] == 5
    assert [entry["level"] for entry in data["entries"]] == [
        "INFO", "INFO", "WARN", "ERROR", "FATAL"
    ]


def test_cli_log_level_filter(instance, home, capsys):
    assert main(["game", "log", str(instance), "--level", "error", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {entry["level"] for entry in data["entries"]} == {"ERROR", "FATAL"}


def test_cli_log_history(instance, home, capsys):
    assert main(["game", "log", str(instance), "--source", "history", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["entry_count"] == 5
    assert data["files"][0].endswith(".log.gz")


def test_cli_log_missing_dir_exits_2(home, capsys, tmp_path):
    assert main(["game", "log", str(tmp_path / "absent"), "--json"]) == 2


def test_cli_crash(instance, home, capsys):
    crash_dir = instance / "crash-reports"
    crash_dir.mkdir()
    (crash_dir / "crash-2026-03-03_09.08.07-client.txt").write_text(
        CRASH_TEXT, encoding="utf-8"
    )
    assert main(["game", "crash", str(instance), "--json"]) == 0
    crash = json.loads(capsys.readouterr().out)["crash"]
    assert crash["found"] is True
    assert crash["description"] == "Ticking entity"
    assert crash["exception"] == "java.lang.NullPointerException"
    assert [mod["name"] for mod in crash["mods"]] == ["create", "forge"]
    assert "0.5.1" in crash["mods"][0]["version"]


def test_cli_crash_none_found(instance, home, capsys):
    assert main(["game", "crash", str(instance), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["crash"]["found"] is False


def test_cli_crash_missing_file_exits_2(instance, home, capsys):
    assert main(["game", "crash", str(instance), "--file",
                 str(instance / "nope.txt")]) == 2


def test_cli_diagnose_attribution(instance, home, capsys):
    assert main(["game", "diagnose", str(instance), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    # SIGNATURES 顺序：java_version 优先于 mod_load（先致命后一般）
    assert data["attribution"] == "java_version"
    assert data["attribution_title"] == "Java 版本不匹配"
    assert data["severity"] == "fatal"
    assert data["suggestion"]
    assert data["evidence"]
    assert data["log_summary"]["errors"] == 2


def test_cli_diagnose_clean_instance(home, tmp_path, capsys):
    clean = tmp_path / "clean"
    (clean / "logs").mkdir(parents=True)
    (clean / "logs" / "latest.log").write_text(
        "[14:00:00] [Server thread/INFO]: Done (1.0s)! For help, type \"help\"\n",
        encoding="utf-8",
    )
    assert main(["game", "diagnose", str(clean), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["severity"] == "ok"
    assert data["attribution"] is None
    assert data["suggestion"] == "未发现错误"


def test_cli_diagnose_crash_and_jvm(instance, home, capsys):
    crash_dir = instance / "crash-reports"
    crash_dir.mkdir()
    (crash_dir / "crash-2026-03-03_09.08.07-client.txt").write_text(
        CRASH_TEXT, encoding="utf-8"
    )
    (instance / "hs_err_pid1.log").write_text(HS_ERR_TEXT, encoding="utf-8")
    assert main(["game", "diagnose", str(instance), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["crash"]["found"] is True
    assert data["jvm"]["found"] is True
    assert data["jvm"]["signal"] == "SIGSEGV"


def test_cli_list_requires_dir(home, capsys):
    assert main(["game", "list"]) == 2


def test_cli_game_help_lists_commands(capsys):
    assert main(["game", "--help"]) == 0
    out = capsys.readouterr().out
    for word in ("log", "crash", "diagnose", "verify", "list"):
        assert word in out
