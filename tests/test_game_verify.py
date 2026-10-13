"""Phase 6 game verify 测试：假进程驱动状态机，不依赖真实 Java。"""

from __future__ import annotations

import json

import pytest

from mmffc.cli import main
from mmffc.game.verify import (
    VerifyError,
    build_plan,
    find_server_jar,
    free_port,
    verify_version,
)

BOOT_LINES = [
    "[14:35:20] [main/INFO]: Loading Minecraft 1.20.1",
    "[14:35:21] [Server thread/INFO]: Starting minecraft server version 1.20.1",
    "[14:35:21] [Server thread/INFO]: Loading properties",
    "[14:35:21] [Server thread/INFO]: Starting Minecraft server on *:25565",
    '[14:35:22] [Server thread/INFO]: Preparing level "world"',
    "[14:35:24] [Server thread/INFO]: Done preparing level \"world\" (2.1s)",
    '[14:35:25] [Server thread/INFO]: Done (3.456s)! For help, type "help"',
    '[14:35:26] [Server thread/INFO]: [mmffc] keep alive check',
]


class FakeProcess:
    """按脚本吐行的假进程（stdout 迭代 + stdin stop + poll）。"""

    def __init__(self, lines, hang=False, exit_after=None):
        self._lines = list(lines)
        self._hang = hang
        self._index = 0
        self.stdin = self._Stdin()
        self.stdout = self._Stdout(self)
        self._bytes = b""

    class _Stdin:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

        def flush(self):
            pass

    class _Stdout:
        def __init__(self, parent):
            self._parent = parent

        def readline(self):
            parent = self._parent
            if parent._index < len(parent._lines):
                line = parent._lines[parent._index]
                parent._index += 1
                return line + "\n"
            if parent._hang:
                import time

                time.sleep(0.05)
                return "\n"
            return ""

    def poll(self):
        return None if self._index < len(self._lines) else 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def _server_dir(tmp_path, name="server-1.20.1"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "minecraft_server.1.20.1.jar").write_bytes(b"PK\x03\x04fake")
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def server_dir(tmp_path):
    return _server_dir(tmp_path)


# ----------------------------------------------------------------------
# jar 发现
# ----------------------------------------------------------------------
def test_find_vanilla_jar(server_dir):
    jar = find_server_jar(server_dir)
    assert jar.loader == "vanilla"
    assert jar.path.name == "minecraft_server.1.20.1.jar"


def test_find_vanilla_by_version(server_dir):
    jar = find_server_jar(server_dir, version="1.20.1")
    assert "1.20.1" in jar.path.name


def test_find_forge_jar(tmp_path):
    root = tmp_path / "forge-server"
    root.mkdir()
    (root / "forge-1.20.1-47.1.0-server.jar").write_bytes(b"PK")
    (root / "forge-1.20.1-47.1.0-installer.jar").write_bytes(b"PK")
    jar = find_server_jar(root)
    assert jar.loader == "forge"
    assert "installer" not in jar.path.name


def test_find_fabric_jar(tmp_path):
    root = tmp_path / "fabric-server"
    root.mkdir()
    (root / "fabric-server-launch.jar").write_bytes(b"PK")
    jar = find_server_jar(root)
    assert jar.loader == "fabric"
    assert jar.needs_libraries is True


def test_find_jar_missing_directory(tmp_path):
    with pytest.raises(VerifyError, match="目录不存在"):
        find_server_jar(tmp_path / "absent")


def test_find_jar_none_present(tmp_path):
    with pytest.raises(VerifyError, match="未找到任何可启动"):
        find_server_jar(tmp_path)


# ----------------------------------------------------------------------
# 计划
# ----------------------------------------------------------------------
def test_build_plan_is_isolated(server_dir):
    plan = build_plan(server_dir, port=25599)
    assert plan.command[0] in ("java", "java.exe") or "java" in plan.command[0]
    assert "--nogui" in plan.command
    assert "--port" in plan.command and "25599" in plan.command
    assert plan.workdir != str(server_dir)
    assert plan.port == 25599
    assert plan.loader == "vanilla"


def test_free_port_differs():
    assert free_port() > 0


# ----------------------------------------------------------------------
# 保活判定（假进程）
# ----------------------------------------------------------------------
def test_verify_alive(server_dir):
    process = FakeProcess(BOOT_LINES)
    events: list[tuple[str, str]] = []
    result = verify_version(
        server_dir,
        timeout=30,
        keep=0.3,
        popen_factory=lambda command, workdir: process,
        on_event=lambda kind, line: events.append((kind, line)),
    )
    assert result.status == "alive"
    assert result.phase == "keep_alive"
    assert result.elapsed_s > 0
    assert result.exit_code == 0
    assert any(kind == "alive" for kind, _ in events)
    assert any(kind == "progress" for kind, _ in events)


def test_verify_boot_failure(server_dir):
    lines = BOOT_LINES[:1] + [
        "java.lang.UnsupportedClassVersionError: class file version 69.0",
    ]
    result = verify_version(
        server_dir, timeout=30, popen_factory=lambda c, w: FakeProcess(lines)
    )
    assert result.status == "boot_failed"
    assert result.phase == "java"
    assert result.attribution == "java_version"
    assert result.suggestion


def test_verify_port_bind_failure(server_dir):
    lines = BOOT_LINES[:3] + ["**** FAILED TO BIND TO PORT! ****"]
    result = verify_version(
        server_dir, timeout=30, popen_factory=lambda c, w: FakeProcess(lines)
    )
    assert result.status == "boot_failed"
    assert result.attribution == "port_bind"


def test_verify_mod_load_failure(server_dir):
    lines = BOOT_LINES[:2] + [
        "Could not load 'mods/foo.jar'",
        "org.modloading.MissingDependencyException",
    ]
    result = verify_version(
        server_dir, timeout=30, popen_factory=lambda c, w: FakeProcess(lines)
    )
    assert result.attribution == "mod_load"


def test_verify_timeout(server_dir):
    lines = BOOT_LINES[:2]  # 永远到不了存活标志
    result = verify_version(
        server_dir,
        timeout=1.0,
        popen_factory=lambda c, w: FakeProcess(lines, hang=True),
    )
    assert result.status == "timeout"
    assert result.attribution == "timeout"
    assert result.attribution_title == "启动超时"


def test_verify_crash_during_keepalive(server_dir):
    lines = BOOT_LINES[:6] + [
        "[14:35:26] [Server thread/FATAL]: java.lang.OutOfMemoryError: Java heap space"
    ]

    result = verify_version(
        server_dir, timeout=30, keep=1.0,
        popen_factory=lambda c, w: FakeProcess(lines, hang=True),
    )
    # 命中存活标志前的 FATAL 短路为 boot_failed
    assert result.status in ("boot_failed", "crashed")
    assert result.attribution in ("oom", "unknown")


def test_verify_eula_failure(server_dir):
    lines = BOOT_LINES[:1] + [
        "You need to agree to the EULA in eula.txt"
    ]
    result = verify_version(
        server_dir, timeout=10, popen_factory=lambda c, w: FakeProcess(lines)
    )
    assert result.attribution == "eula"


# ----------------------------------------------------------------------
# CLI 契约
# ----------------------------------------------------------------------
def test_cli_verify_dry_run(server_dir, home, capsys):
    assert main(["game", "verify", str(server_dir), "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True
    assert plan["command"]
    assert plan["jar"].endswith("minecraft_server.1.20.1.jar")
    assert plan["port"] > 0


def test_cli_verify_missing_jar_exits_7(home, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["game", "verify", str(empty), "--json"]) == 7
    assert "未找到" in capsys.readouterr().err


def test_cli_verify_requires_yes(server_dir, home, capsys, monkeypatch):
    monkeypatch.setattr(
        "mmffc.cli.game.verify_version",
        lambda *a, **kw: verify_version(
            a[0], timeout=0.5,
            popen_factory=lambda c, w: FakeProcess(BOOT_LINES),
        ),
    )
    assert main(["game", "verify", str(server_dir), "--json"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_cli_verify_alive_exit_0(server_dir, home, capsys, monkeypatch):
    def fake_verify(game_dir, **kwargs):
        return verify_version(
            game_dir,
            timeout=30,
            popen_factory=lambda c, w: FakeProcess(BOOT_LINES),
        )

    monkeypatch.setattr("mmffc.cli.game.verify_version", fake_verify)
    assert main(["game", "verify", str(server_dir), "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "alive"


def test_cli_verify_failure_exits_7(server_dir, home, capsys, monkeypatch):
    def fake_verify(game_dir, **kwargs):
        return verify_version(
            game_dir,
            timeout=30,
            popen_factory=lambda c, w: FakeProcess(
                BOOT_LINES[:1] + ["UnsupportedClassVersionError: nope"]
            ),
        )

    monkeypatch.setattr("mmffc.cli.game.verify_version", fake_verify)
    assert main(["game", "verify", str(server_dir), "--yes", "--json"]) == 7
    assert "保活失败" in capsys.readouterr().err
