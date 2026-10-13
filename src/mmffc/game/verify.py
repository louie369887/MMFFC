"""版本保活验证（Phase 6）：真实启动服务端并监视存活标志。

判定流程（CLI.md 3.8 / prd.md Phase 6）::

    发现 jar -> 分配空闲端口 -> 隔离临时 gameDir（拷贝 jar + 联接 libraries）
      -> 写 eula.txt -> java -jar <jar> --nogui --universe <tmp> --port <port>
      -> 逐行读输出：进度标志 / 失败标志短路
      -> 命中存活标志 "Done (<n>.<n>s)! For help, type" -> --keep N 秒保活观察
      -> 优雅停止（stdin 发 stop）-> 清理临时目录 -> 输出结构化报告

隔离保证：绝不在用户实例上启动；世界写入临时 universe；端口随机。
输出读取由后台线程 + 队列完成，主循环按 deadline 轮询，避免进程挂起时死等。
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

LOADERS = ("auto", "vanilla", "forge", "fabric", "neoforge")

ALIVE_RE = re.compile(r"Done\s*\(\s*\d+(?:\.\d+)?s\)!?\s*For help, type")
PROGRESS_MARKERS: tuple[tuple[str, str], ...] = (
    ("java", r"UnsupportedClassVersionError|Could not find or load main class"),
    ("bootstrap", r"Starting minecraft server version|Loading Minecraft|Loading mods"),
    ("properties", r"Loading properties"),
    ("bind", r"Starting Minecraft server on \*?:"),
    ("world_load", r"Preparing level|Preparing spawn area|Loaded .* advancements"),
    ("spawn_ready", r"Prepared spawn area|Done preparing level"),
)
FAILURE_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("java", r"UnsupportedClassVersionError", "java_version"),
    (
        "java",
        r"Could not find or load main class|Error: A JNI error|NoClassDefFoundError",
        "java_version",
    ),
    ("bootstrap", r"You need to agree to the EULA|Mojang EULA", "eula"),
    (
        "bind",
        r"FAILED TO BIND TO PORT|BindException|Address already in use",
        "port_bind",
    ),
    (
        "mod_load",
        r"Could not load|Unknown/missing dependency|ModResolutionException"
        r"|Missing or unsupported mandatory dependencies|Failed to create mod instance",
        "mod_load",
    ),
    ("crash", r"Exception in thread \"main\"|---- Minecraft Crash Report ----", "unknown"),
    ("world_load", r"Failed to read chunk|Corrupt chunk|Failed to load level", "world_corrupt"),
    ("keep_alive", r"OutOfMemoryError|Server (?:closed|stopped unexpectedly)", "oom"),
)
EULA_TEXT = "eula=true\n"
STOP_KEYWORD = "stop"
STOP_TIMEOUT = 30.0
TERMINATE_TIMEOUT = 10.0


@dataclass
class ServerJar:
    """发现的服务端启动载体。"""

    path: Path
    loader: str
    version: str | None = None
    needs_libraries: bool = False


@dataclass
class VerifyPlan:
    """--dry-run 输出的执行计划（不启动任何进程）。"""

    command: list[str]
    workdir: str
    jar: str
    loader: str
    port: int
    universe: str
    memory: str

    def to_dict(self) -> dict:
        return {
            "dry_run": True,
            "command": self.command,
            "workdir": self.workdir,
            "jar": self.jar,
            "loader": self.loader,
            "port": self.port,
            "universe": self.universe,
            "memory": self.memory,
        }


@dataclass
class VerifyResult:
    """一次保活验证的结论。"""

    status: str                # alive | boot_failed | crashed | timeout
    phase: str                 # java | bootstrap | ... | keep_alive | done
    command: list[str]
    workdir: str
    jar: str
    loader: str
    port: int
    elapsed_s: float
    attribution: str | None = None
    attribution_title: str | None = None
    suggestion: str | None = None
    evidence: list[str] = field(default_factory=list)
    exit_code: int | None = None
    log_path: str | None = None
    probe: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.status == "alive",
            "status": self.status,
            "phase": self.phase,
            "elapsed_s": round(self.elapsed_s, 3),
            "command": self.command,
            "workdir": self.workdir,
            "jar": self.jar,
            "loader": self.loader,
            "port": self.port,
            "attribution": self.attribution,
            "attribution_title": self.attribution_title,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "exit_code": self.exit_code,
            "log_path": self.log_path,
            "probe": self.probe,
        }


class VerifyError(Exception):
    """启动前置条件不满足（jar 缺失、Java 不可用等）。"""


def find_server_jar(
    game_dir: Path, *, loader: str = "auto", version: str | None = None
) -> ServerJar:
    """定位服务端启动载体。"""
    root = Path(game_dir)
    if not root.is_dir():
        raise VerifyError(f"目录不存在: {root}")

    if loader == "vanilla":
        jars = _vanilla_jars(root, version)
        if not jars:
            raise VerifyError(
                f"未找到 vanilla 服务端 jar（期望 minecraft_server.<version>.jar"
                f" 或 server.jar）: {root}"
            )
        return ServerJar(jars[0], "vanilla", version)

    if loader == "fabric":
        fabric = root / "fabric-server-launch.jar"
        if not fabric.is_file():
            raise VerifyError(f"未找到 fabric-server-launch.jar: {root}")
        return ServerJar(fabric, "fabric", version, needs_libraries=True)

    if loader in ("forge", "neoforge"):
        jar = _loader_jar(root, loader)
        if jar is None:
            raise VerifyError(f"未找到 {loader} 服务端 jar: {root}")
        return ServerJar(jar, loader, version)

    for candidate in ("neoforge", "forge"):
        jar = _loader_jar(root, candidate)
        if jar is not None:
            return ServerJar(jar, candidate, version)
    fabric = root / "fabric-server-launch.jar"
    if fabric.is_file():
        return ServerJar(fabric, "fabric", version, needs_libraries=True)
    vanilla = _vanilla_jars(root, version)
    if vanilla:
        return ServerJar(vanilla[0], "vanilla", version)
    raise VerifyError(f"未找到任何可启动的服务端 jar: {root}")


def _vanilla_jars(root: Path, version: str | None) -> list[Path]:
    if version:
        exact = root / f"minecraft_server.{version}.jar"
        if exact.is_file():
            return [exact]
    found = sorted(root.glob("minecraft_server.*.jar"), key=lambda path: path.name)
    if (root / "server.jar").is_file():
        found.append(root / "server.jar")
    return found


def _loader_jar(root: Path, loader: str) -> Path | None:
    found = sorted(root.glob(f"{loader}-*-*server*.jar"), key=lambda path: path.name)
    found = [
        path
        for path in found
        if "installer" not in path.name.lower() and path.is_file()
    ]
    return found[0] if found else None


def free_port() -> int:
    """分配一个随机空闲 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_java(java: str | None) -> str:
    """解析 JVM 路径：--java > MMFFC_JAVA/JAVA_HOME > PATH 上的 java。"""
    if java:
        return java
    for env_name in ("MMFFC_JAVA", "JAVA_HOME"):
        value = os.environ.get(env_name)
        if value:
            candidate = Path(value)
            executable = (
                candidate / "bin" / ("java.exe" if os.name == "nt" else "java")
                if candidate.is_dir()
                else candidate
            )
            return str(executable)
    return "java"


def build_plan(
    game_dir: Path,
    *,
    loader: str = "auto",
    version: str | None = None,
    java: str | None = None,
    memory: str = "1024M",
    workdir: str | None = None,
    port: int | None = None,
) -> VerifyPlan:
    """组装启动计划（不启动进程）。"""
    jar_info = find_server_jar(game_dir, loader=loader, version=version)
    target_port = port if port is not None else free_port()
    work = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="mmffc-verify-")
    )
    jar_dest = work / "server-verify.jar"
    command = [
        resolve_java(java),
        f"-Xmx{memory}",
        "-jar",
        str(jar_dest),
        "--nogui",
        "--universe",
        str(work / "universe"),
        "--port",
        str(target_port),
    ]
    return VerifyPlan(
        command=command,
        workdir=str(work),
        jar=str(jar_info.path),
        loader=jar_info.loader,
        port=target_port,
        universe=str(work / "universe"),
        memory=memory,
    )


def _prepare_workdir(game_dir: Path, plan: VerifyPlan, jar_info: ServerJar) -> None:
    """搭建隔离工作目录：jar 拷贝 + libraries 联接 + eula.txt。"""
    work = Path(plan.workdir)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar_info.path, Path(plan.command[3]))
    if jar_info.needs_libraries:
        for name in ("libraries", "versions", "mods", "config"):
            source = Path(game_dir) / name
            if source.is_dir():
                _link_or_copy(source, work / name)
    eula = work / "eula.txt"
    if not eula.exists():
        eula.write_text(EULA_TEXT, encoding="utf-8")


def _link_or_copy(src: Path, dest: Path) -> None:
    """优先创建目录链接；失败则忽略（jar 自身可能已自包含）。"""
    if dest.exists() or dest.is_symlink():
        return
    try:
        os.symlink(src, dest, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":  # pragma: no cover - Windows junction 兜底
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(dest), str(src)],
                capture_output=True,
                check=False,
            )
        except OSError:
            pass


def _spawn(command: list[str], workdir: str):
    return subprocess.Popen(
        command,
        cwd=workdir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )


class _LineReader:
    """后台线程读行 -> 队列，避免主循环被挂起的进程阻塞。"""

    def __init__(self, stream) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stream = stream
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for line in iter(self._stream.readline, ""):
                self._queue.put(line)
        finally:
            self._queue.put(None)

    def get(self, timeout: float) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return ""


def _stop_gracefully(process) -> None:
    """stdin 发 stop，最多等 STOP_TIMEOUT 秒，否则 terminate。"""
    try:
        if process.stdin is not None:
            process.stdin.write(STOP_KEYWORD + "\n")
            process.stdin.flush()
    except (OSError, ValueError):
        pass
    thread = threading.Thread(target=process.wait, daemon=True)
    thread.start()
    thread.join(timeout=STOP_TIMEOUT)
    if process.poll() is None:
        _shutdown(process)


def _shutdown(process) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=TERMINATE_TIMEOUT)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass


def _safe_exit(process) -> int | None:
    try:
        return process.poll()
    except Exception:  # pragma: no cover
        return None


def _probe_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _server_log(workdir: str) -> str | None:
    path = Path(workdir) / "logs" / "latest.log"
    return str(path) if path.is_file() else None


def cleanup(workdir: str) -> None:
    """清理临时工作目录（尽力而为）。"""
    shutil.rmtree(workdir, ignore_errors=True)


def verify_version(
    game_dir: Path,
    *,
    loader: str = "auto",
    version: str | None = None,
    java: str | None = None,
    memory: str = "1024M",
    timeout: float = 300.0,
    keep: float = 0.0,
    port: int | None = None,
    probe: bool = False,
    on_event=None,
    popen_factory=None,
) -> VerifyResult:
    """执行一次保活验证（详见模块 docstring）。

    ``on_event(kind, line)`` 逐行回调（kind: raw/progress/failure/alive），
    供 CLI 显示进度；测试可用 ``popen_factory`` 注入假进程。
    """
    from mmffc.game.attribution import SUGGESTIONS, TITLES

    started = time.monotonic()
    jar_info = find_server_jar(game_dir, loader=loader, version=version)
    plan = build_plan(
        game_dir, loader=loader, version=version, java=java,
        memory=memory, port=port,
    )
    _prepare_workdir(game_dir, plan, jar_info)

    spawn = popen_factory or _spawn
    try:
        process = spawn(plan.command, plan.workdir)
    except FileNotFoundError as exc:
        raise VerifyError(
            f"找不到 Java 可执行文件: {plan.command[0]}（用 --java 指定 JVM 路径）"
        ) from exc
    except OSError as exc:
        raise VerifyError(f"启动失败: {exc}") from exc

    reader = _LineReader(process.stdout)

    def emit(kind: str, line: str) -> None:
        if on_event is not None:
            on_event(kind, line)

    evidence: list[str] = []

    def result(
        status: str,
        phase: str,
        *,
        attribution: str | None = None,
        extra: dict | None = None,
    ) -> VerifyResult:
        payload: dict = {}
        if attribution is not None:
            payload = {
                "attribution": attribution,
                "attribution_title": TITLES.get(attribution, attribution),
                "suggestion": SUGGESTIONS.get(attribution, ""),
            }
        if extra:
            payload.update(extra)
        return VerifyResult(
            status=status,
            phase=phase,
            command=plan.command,
            workdir=plan.workdir,
            jar=plan.jar,
            loader=plan.loader,
            port=plan.port,
            elapsed_s=time.monotonic() - started,
            evidence=evidence[-20:],
            exit_code=_safe_exit(process),
            log_path=_server_log(plan.workdir),
            probe=probe,
            **payload,
        )

    def failure(phase: str, line: str, attribution: str) -> VerifyResult:
        return result(
            "crashed" if phase == "keep_alive" else "boot_failed",
            phase,
            attribution=attribution,
        )

    # ---- 启动阶段：等待存活标志或失败/超时 ----
    phase = "launch"
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                _shutdown(process)
                return result(
                    "timeout",
                    phase,
                    attribution="timeout",
                    extra={
                        "attribution_title": "启动超时",
                        "suggestion": (
                            f"在 {timeout:.0f}s 内未达到存活标志；大型整合包首启"
                            "可能更久，可用 --timeout 增大，并用 --dry-run 检查命令"
                        ),
                    },
                )
            line = reader.get(min(remaining, 1.0))
            if line is None:
                break  # 进程退出
            if line == "":
                continue
            raw = line.rstrip("\r\n")
            emit("raw", raw)
            if not raw.strip():
                continue
            evidence.append(raw)
            if len(evidence) > 200:
                evidence.pop(0)

            for marker, pattern in PROGRESS_MARKERS:
                if re.search(pattern, raw):
                    phase = marker
                    emit("progress", raw)
                    break
            for marker, pattern, attribution in FAILURE_SIGNATURES:
                if re.search(pattern, raw):
                    emit("failure", raw)
                    _shutdown(process)
                    return failure(marker, raw, attribution)
            if ALIVE_RE.search(raw):
                emit("alive", raw)
                break
        else:  # pragma: no cover - not reached
            pass
    finally:
        pass

    # ---- 保活阶段 ----
    if keep > 0:
        keep_started = time.monotonic()
        while time.monotonic() - keep_started < keep:
            line = reader.get(0.5)
            if line is None:
                break
            if line == "":
                continue
            raw = line.rstrip("\r\n")
            emit("raw", raw)
            if not raw.strip():
                continue
            evidence.append(raw)
            if len(evidence) > 200:
                evidence.pop(0)
            for marker, pattern, attribution in FAILURE_SIGNATURES:
                if re.search(pattern, raw):
                    emit("failure", raw)
                    _shutdown(process)
                    return failure(marker, raw, attribution)

    if probe:
        emit("raw", f"[mmffc] probe {plan.port}")
        _probe_port(plan.port)

    _stop_gracefully(process)
    return result("alive", "keep_alive" if keep > 0 else "done")
