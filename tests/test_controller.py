import copy
import io
import json
import math
import struct
import threading
import unittest
from unittest.mock import patch

from jev_factorio.controller import Settings, offline_demo, run
from jev_factorio.jev import Decision, Jev, make_request, parse_decision
from jev_factorio.rcon import BridgeError, Rcon


STATE = {"ok": True, "protocol": 1, "task_protocol": 1, "armed": True, "observation": "0:1", "tick": 0,
         "busy_ticks": 0, "inventory": [], "actions": [
             {"id": "wait", "description": "Wait"}, {"id": "stop", "description": "Stop"},
             {"id": "a3", "description": "Mine iron"}]}
ANSWER = {"model": "jev-1.13.0", "answers": {"action": {"type": "choice", "choice": "a3",
          "confidence": .8, "probabilities": {"wait": .1, "stop": .1, "a3": .8}}},
          "usage": {"input_tokens": 123}}


class FakeBridge:
    def __init__(self, *args):
        self.operations = []
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def call(self, operation, **payload):
        self.operations.append((operation, payload))
        return copy.deepcopy(STATE) if operation == "observe" else {"ok": True, "result": "accepted"}


class FakeModel:
    def __init__(self, *args): self.calls = 0
    def decide(self, *args):
        self.calls += 1
        return Decision("a3", .8, .8, "test-model", 123)


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.payload = make_request(STATE, "Smelt iron", [], "jev-latest")
        self.criteria = self.payload["questions"]["action"]["criteria"]

    def test_wire_contract(self):
        self.assertEqual(self.payload["questions"]["action"]["type"], "choice")
        self.assertEqual(self.criteria, {"wait": "Wait", "stop": "Stop", "a3": "Mine iron"})
        self.assertEqual(parse_decision(ANSWER, self.criteria).input_tokens, 123)

    def test_reject_unknown_action(self):
        answer = copy.deepcopy(ANSWER)
        answer["answers"]["action"]["choice"] = "/c evil()"
        with self.assertRaises(BridgeError): parse_decision(answer, self.criteria)

    def test_invalid_confidence_and_distribution(self):
        for field, value in [("confidence", float("nan")), ("confidence", 2), ("confidence", True),
                             ("probabilities", {"a3": 1}), ("probabilities", {"a3": .8, "wait": .8, "stop": .8}),
                             ("choice", "wait"), ("type", "score")]:
            with self.subTest(field=field, value=value):
                answer = copy.deepcopy(ANSWER)
                answer["answers"]["action"][field] = value
                with self.assertRaises(BridgeError): parse_decision(answer, self.criteria)

    def test_duplicate_actions(self):
        state = copy.deepcopy(STATE)
        state["actions"].append(state["actions"][0])
        with self.assertRaises(BridgeError): make_request(state, "goal", [], "jev-latest")

    def test_actual_http_adapter_with_fake_transport(self):
        client = Jev("fake-test-key")
        captured = []
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                return Response(json.dumps(ANSWER).encode())
        client.opener = Opener()
        self.assertEqual(client.decide(STATE, "goal", []).choice, "a3")
        self.assertEqual(captured[0].full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer fake-test-key")
        self.assertEqual(json.loads(captured[0].data)["model"], "jev-latest")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.bridge, self.model = FakeBridge(), FakeModel()
        self.stop = threading.Event()
        self.messages = []
        self.settings = Settings(max_decisions=1, interval=.25, autonomous=False, min_confidence=.5)

    def run_loop(self, execute):
        run(self.settings, "test-key", "test-password", execute, self.stop, self.messages.append,
            bridge_factory=lambda *a: self.bridge, model_factory=lambda *a: self.model)

    def test_preview_never_arms_or_acts_or_stops_player(self):
        self.run_loop(False)
        self.assertEqual([op for op, _ in self.bridge.operations], ["observe", "observe"])

    def test_live_is_bounded_and_stops(self):
        self.run_loop(True)
        ops = [op for op, _ in self.bridge.operations]
        self.assertEqual(ops, ["observe", "start", "observe", "act", "stop"])
        self.assertEqual(self.model.calls, 1)
        self.assertTrue(self.bridge.closed)
        self.assertEqual(self.bridge.operations[3][1]["action"], "a3")

    def test_cancel_during_api_sends_no_action(self):
        original = self.model.decide
        def decide(*args):
            self.stop.set()
            return original(*args)
        self.model.decide = decide
        self.run_loop(True)
        self.assertNotIn("act", [op for op, _ in self.bridge.operations])
        self.assertEqual(self.bridge.operations[-1][0], "stop")

    def test_low_confidence_never_acts(self):
        self.settings.max_decisions = 10
        self.model.decide = lambda *a: Decision("a3", .1, .4, "test-model", 1)
        self.run_loop(True)
        self.assertNotIn("act", [op for op, _ in self.bridge.operations])
        self.assertTrue(any("three uncertain" in m for m in self.messages))

    def test_failure_releases_control(self):
        def fail(*args): raise BridgeError("API unavailable")
        self.model.decide = fail
        with self.assertRaises(BridgeError): self.run_loop(True)
        self.assertEqual(self.bridge.operations[-1][0], "stop")

    def test_invalid_settings_no_connection(self):
        self.settings.max_minutes = float("nan")
        with self.assertRaises(BridgeError): self.run_loop(True)
        self.assertEqual(self.bridge.operations, [])

    def test_offline_demo_no_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("Network forbidden")):
            offline_demo(self.messages.append)
        self.assertTrue(any("Offline demo complete" in m for m in self.messages))


def packet(request_id, kind, body):
    if isinstance(body, str): body = body.encode()
    payload = struct.pack("<ii", request_id, kind) + body + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


class FakeSocket:
    def __init__(self, incoming):
        self.data = bytearray(incoming)
        self.sent = []
    def recv(self, n):
        n = min(n, 3)  # TCP can fragment anywhere, including size prefixes.
        part = bytes(self.data[:n])
        del self.data[:n]
        return part
    def sendall(self, data): self.sent.append(data)
    def settimeout(self, value): pass
    def close(self): pass


class RconTests(unittest.TestCase):
    def test_auth_empty_packet_and_fragmentation(self):
        sock = FakeSocket(packet(1, 0, "") + packet(1, 2, ""))
        with patch("socket.create_connection", return_value=sock) as connect:
            with Rcon(27015, "secret"):
                pass
        self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 27015))

    def test_auth_denied(self):
        with patch("socket.create_connection", return_value=FakeSocket(packet(-1, 2, ""))):
            with self.assertRaises(BridgeError): Rcon(27015, "bad").__enter__()

    def test_multipart_utf8_and_barrier(self):
        class UUID:
            hex = "abc123"
        value = json.dumps({"ok": True, "label": "café"}, ensure_ascii=False).encode()
        split = value.index(b"\xc3") + 1
        sock = FakeSocket(packet(12, 0, value[:split]) + packet(12, 0, value[split:]) + packet(13, 0, "abc123\n"))
        client = Rcon(27015, "secret")
        client.sock = sock
        with patch("jev_factorio.rcon.uuid.uuid4", return_value=UUID()):
            result = client.call("players")
        self.assertEqual(result["label"], "café")
        self.assertEqual(len(sock.sent), 2)

    def test_truncated_packet_and_invalid_length_fail_closed(self):
        for wire in [struct.pack("<i", -1), packet(1, 0, "hello")[:-1]]:
            client = Rcon(27015, "secret")
            client.sock = FakeSocket(wire)
            with self.assertRaises(BridgeError): client._receive()

    def test_no_arbitrary_protocol_operation(self):
        with self.assertRaises(BridgeError): Rcon(27015, "secret").call("lua", code="bad")


if __name__ == "__main__":
    unittest.main()
