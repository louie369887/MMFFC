"""RCON client tests against a fake standard-compliant server."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from mmffc.cli import main
from mmffc.core.errors import NetworkError, PermissionDeniedError
from mmffc.net.rcon import RconClient, reload_commands

HEADER = "<iii"


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = conn.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_packet(conn: socket.socket) -> tuple[int, int, str]:
    """Read one packet using Source RCON standard framing."""
    length, request_id, packet_type = struct.unpack(
        HEADER, _recv_exact(conn, 12)
    )
    payload = _recv_exact(conn, length - 8)
    return request_id, packet_type, payload[:-2].decode("utf-8")


def _write_packet(
    conn: socket.socket, request_id: int, packet_type: int, body: str
) -> None:
    """Write one packet using Source RCON standard framing."""
    payload = body.encode("utf-8") + b"\x00\x00"
    header = struct.pack(HEADER, len(payload) + 8, request_id, packet_type)
    conn.sendall(header + payload)


class FakeRconServer:
    """Standard-compliant in-process RCON server for tests."""

    def __init__(
        self,
        password: str = "secret",
        responses: dict[str, list[str]] | None = None,
    ) -> None:
        self.password = password
        # command -> list of response packets (split output)
        self.responses = responses or {}
        self.received: list[str] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        try:
            with conn:
                authenticated = False
                while True:
                    try:
                        request_id, packet_type, body = _read_packet(conn)
                    except (ConnectionError, OSError):
                        return
                    if packet_type == 3:  # SERVERDATA_AUTH
                        if body == self.password:
                            _write_packet(conn, request_id, 0, "")
                            _write_packet(conn, request_id, 2, "")
                            authenticated = True
                        else:
                            _write_packet(conn, -1, 2, "")
                        continue
                    if packet_type == 2:  # SERVERDATA_EXECCOMMAND
                        if not authenticated:
                            _write_packet(conn, -1, 2, "")
                            continue
                        self.received.append(body)
                        parts = self.responses.get(body, [f"ok:{body}"])
                        for part in parts:
                            _write_packet(conn, request_id, 0, part)
                        continue
        finally:
            self._listener.close()

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=5)


@pytest.fixture
def server_factory():
    servers: list[FakeRconServer] = []

    def factory(**kwargs) -> FakeRconServer:
        server = FakeRconServer(**kwargs)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


def test_auth_and_execute(server_factory):
    server = server_factory(password="hunter2")
    with RconClient("127.0.0.1", server.port, "hunter2") as client:
        output = client.execute("list")
    assert output == "ok:list"
    assert server.received == ["list"]


def test_auth_failure(server_factory):
    server = server_factory(password="right")
    client = RconClient("127.0.0.1", server.port, "wrong")
    with pytest.raises(PermissionDeniedError):
        client.connect()
    client.close()


def test_multi_packet_output(server_factory):
    # large outputs are split into multiple response packets
    server = server_factory(
        responses={"big": ["chunk-one ", "chunk-two"]}
    )
    with RconClient("127.0.0.1", server.port, "secret") as client:
        output = client.execute("big")
    assert output == "chunk-one chunk-two"


def test_connection_refused():
    # grab a port that is then closed
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    client = RconClient("127.0.0.1", port, "x", timeout=2.0)
    with pytest.raises(NetworkError):
        client.connect()
    client.close()


def test_execute_requires_connection():
    client = RconClient("127.0.0.1", 1, "x")
    with pytest.raises(NetworkError):
        client.execute("list")


def test_context_manager_closes(server_factory):
    server = server_factory()
    with RconClient("127.0.0.1", server.port, "secret") as client:
        assert client._socket is not None
    assert client._socket is None


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def test_reload_commands_mapping():
    assert reload_commands("fancymenu") == ["fancymenu reload"]
    assert reload_commands("ftbquests") == ["ftbquests reload"]
    assert reload_commands("all") == [
        "fancymenu reload",
        "ftbquests reload",
    ]
    with pytest.raises(ValueError):
        reload_commands("nope")


def test_rcon_exec_requires_password(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
    monkeypatch.delenv("MMFFC_RCON_PASSWORD", raising=False)
    code = main(["rcon", "exec", "list"])
    assert code == 2
    assert "密码" in capsys.readouterr().err


def test_rcon_exec_roundtrip(tmp_path, monkeypatch, capsys):
    server = FakeRconServer(password="pw")
    try:
        monkeypatch.setenv("MMFFC_HOME", str(tmp_path))
        monkeypatch.setenv("MMFFC_RCON_PASSWORD", "pw")
        code = main(
            [
                "rcon",
                "exec",
                "list",
                "--port",
                str(server.port),
            ]
        )
        assert code == 0
        assert capsys.readouterr().out == "ok:list"
        assert server.received == ["list"]
    finally:
        server.close()
