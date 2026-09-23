"""Minimal Source RCON client with TCP reassembly and a reply barrier."""

import json
import socket
import struct
import uuid


class BridgeError(RuntimeError):
    pass


class Rcon:
    def __init__(self, port, password, timeout=5):
        # This application deliberately connects only to this computer.
        self.port, self.password, self.timeout = port, password, timeout
        self.sock = None
        self.sequence = 10

    def __enter__(self):
        try:
            self.sock = socket.create_connection(("127.0.0.1", self.port), self.timeout)
            self.sock.settimeout(self.timeout)
            self._send(1, 3, self.password)
            for _ in range(8):
                request_id, kind, _ = self._receive()
                if request_id == -1:
                    raise BridgeError("RCON password was rejected.")
                if request_id == 1 and kind == 2:
                    return self
            raise BridgeError("RCON authentication response was invalid.")
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __exit__(self, *args):
        self.close()

    def _exact(self, size):
        result = bytearray()
        while len(result) < size:
            part = self.sock.recv(size - len(result))
            if not part:
                raise BridgeError("Factorio closed the RCON connection.")
            result.extend(part)
        return bytes(result)

    def _receive(self):
        length, = struct.unpack("<i", self._exact(4))
        if not 10 <= length <= 4_194_304:
            raise BridgeError("Invalid RCON packet size.")
        data = self._exact(length)
        if data[-2:] != b"\0\0":
            raise BridgeError("Invalid RCON packet terminator.")
        request_id, kind = struct.unpack("<ii", data[:8])
        return request_id, kind, data[8:-2]

    def _send(self, request_id, kind, value):
        if "\0" in value or "\n" in value or "\r" in value:
            raise BridgeError("RCON messages must be a single line without NUL bytes.")
        body = struct.pack("<ii", request_id, kind) + value.encode("utf-8") + b"\0\0"
        self.sock.sendall(struct.pack("<i", len(body)) + body)

    def command(self, command):
        self.sequence += 2
        request_id, barrier_id = self.sequence, self.sequence + 1
        marker = uuid.uuid4().hex
        self._send(request_id, 2, command)
        # The mod replies to this after the preceding command has completed.
        # A timeout or packet length is NOT treated as successful completion.
        self._send(barrier_id, 2, "/jev-ping " + marker)
        chunks, barrier = [], bytearray()
        for _ in range(2048):
            rid, kind, data = self._receive()
            if kind != 0:
                raise BridgeError("Unexpected RCON response type.")
            if rid == request_id:
                chunks.append(data)
                if sum(map(len, chunks)) > 2_000_000:
                    raise BridgeError("Factorio reply is too large.")
            elif rid == barrier_id:
                barrier.extend(data)
                if marker.encode() in barrier:
                    return b"".join(chunks).decode("utf-8").strip()
                if len(barrier) > 1024:
                    break
        raise BridgeError("Missing bridge reply. Check that the Jev mod is enabled.")

    def call(self, operation, **payload):
        if operation not in {"observe", "start", "act", "stop", "players", "catalog"}:
            raise BridgeError("Unknown bridge operation.")
        raw = self.command("/jev-" + operation + " " + json.dumps(payload, separators=(",", ":")))
        try:
            result = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise BridgeError("Invalid bridge reply. Enable the Jev mod on the host.") from exc
        if not isinstance(result, dict) or not result.get("ok"):
            detail = result.get("error", "No response") if isinstance(result, dict) else "Invalid response"
            raise BridgeError(str(detail))
        return result
