"""RCON 协议客户端（原生 socket 实现，无第三方依赖）。

协议细节（Source RCON）：

* 明文 TCP，建议仅限本地连接或通过 SSH 隧道使用
* 数据包：``<len:int32><id:int32><type:int32><body><empty:int32>``
  全部小端序，body 以 NUL 结尾；len = id(4) + type(4) +
  body + 2 个 NUL（即长度字段之后的全部字节数，Source RCON 标准）
* type 3 = 认证，type 2 = 执行命令，type 0 = 响应，
  type -1 = 认证失败
* 大输出可能拆分为多个数据包

单人游戏（无 RCON）应使用文件监听方案
（见 :func:`watch_config_directory`）。
"""

from __future__ import annotations

import socket
import struct

from mmffc.core.errors import NetworkError, PermissionDeniedError

SERVERDATA_RESPONSE = 0
SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2

HEADER = "<iii"
HEADER_SIZE = struct.calcsize(HEADER)
MAX_PACKET_SIZE = 4096


class RconClient:
    """Minimal Source RCON client."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 25575,
        password: str = "",
        *,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._request_id = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        try:
            self._socket = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as exc:
            raise NetworkError(
                f"RCON 连接失败 {self.host}:{self.port}: {exc}"
            ) from exc
        self._authenticate()

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # protocol
    # ------------------------------------------------------------------
    def _authenticate(self) -> None:
        if self._socket is None:
            raise NetworkError("RCON 未连接")
        request_id = self._next_id()
        self._send(request_id, SERVERDATA_AUTH, self.password)
        responses = self._receive_multi(request_id)
        for response_id, response_type, _body in responses:
            if response_id == -1 or response_type == -1:
                raise PermissionDeniedError("RCON 认证失败 (密码错误)")
        if not responses:
            raise NetworkError("RCON 认证无响应")

    def execute(self, command: str) -> str:
        """Execute a command and return its console output."""
        if self._socket is None:
            raise NetworkError("RCON 未连接")
        request_id = self._next_id()
        self._send(request_id, SERVERDATA_EXECCOMMAND, command)
        responses = self._receive_multi(request_id)
        bodies = [
            body
            for response_id, response_type, body in responses
            if response_type == SERVERDATA_RESPONSE
        ]
        return "".join(bodies)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id & 0x7FFFFFFF

    def _send(self, request_id: int, packet_type: int, body: str) -> None:
        if self._socket is None:
            raise NetworkError("RCON 未连接")
        payload = body.encode("utf-8") + b"\x00\x00"
        # Length counts everything after the length field:
        # request id (4) + packet type (4) + payload.
        header = struct.pack(
            HEADER, len(payload) + 8, request_id, packet_type
        )
        try:
            self._socket.sendall(header + payload)
        except OSError as exc:
            raise NetworkError(f"RCON 发送失败: {exc}") from exc

    def _receive_packet(self) -> tuple[int, int, str]:
        if self._socket is None:
            raise NetworkError("RCON 未连接")
        header = self._recv_exact(HEADER_SIZE)
        length, request_id, packet_type = struct.unpack(
            HEADER, header
        )
        # Minimum: request id (4) + packet type (4) + 2 NUL bytes.
        if length < 10 or length > MAX_PACKET_SIZE * 4:
            raise NetworkError(
                f"RCON 数据包长度非法: {length}"
            )
        # 8 header bytes (id + type) already consumed from `length`.
        payload = self._recv_exact(length - 8)
        # payload ends with two NUL bytes
        body = payload[:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, body

    def _receive_multi(
        self, request_id: int
    ) -> list[tuple[int, int, str]]:
        """Read packets until a short idle window passes.

        Large command outputs are split across multiple packets;
        we keep reading while the socket keeps producing data.
        """
        packets: list[tuple[int, int, str]] = []
        if self._socket is None:
            return packets
        previous_timeout = self._socket.gettimeout()
        try:
            self._socket.settimeout(0.5)
            while True:
                try:
                    packet = self._receive_packet()
                except (NetworkError, TimeoutError, socket.timeout):
                    break
                except OSError:
                    break
                if packet[0] in (request_id, -1):
                    packets.append(packet)
        finally:
            self._socket.settimeout(previous_timeout)
        return packets

    def _recv_exact(self, size: int) -> bytes:
        if self._socket is None:
            raise NetworkError("RCON 未连接")
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            try:
                chunk = self._socket.recv(remaining)
            except socket.timeout as exc:
                raise NetworkError("RCON 读取超时") from exc
            except OSError as exc:
                raise NetworkError(f"RCON 读取失败: {exc}") from exc
            if not chunk:
                raise NetworkError("RCON 连接被关闭")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def reload_commands(target: str) -> list[str]:
    """In-game commands that trigger a config reload."""
    if target == "fancymenu":
        return ["fancymenu reload"]
    if target == "ftbquests":
        return ["ftbquests reload"]
    if target == "all":
        return ["fancymenu reload", "ftbquests reload"]
    raise ValueError(f"未知的重载目标: {target!r}")
