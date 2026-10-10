"""Offline synthetic bookkeeping/native-vector parity only: no runtime or proof evidence."""

import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from fairvenue_schema6 import (
    AgentClient, AgentError, CryptoPrimitives, HttpTransport, MemorySigner,
    build_agent_command_signables,
    load_v1_agent_key,
)
from fairvenue_schema6.client import _json

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/agent-commands.golden.json").read_text())
SOURCE = (ROOT / "fairvenue_schema6/client.py").read_text()
assert SOURCE.count("if not False:") == 2
# Labelled in-memory mutation-gate evaluation ONLY. No enabled file, CLI,
# environment override, exported bypass, real ingress or authority acceptance.
MOCK = types.ModuleType("fairvenue_schema6._synthetic_client")
MOCK.__package__ = "fairvenue_schema6"
sys.modules[MOCK.__name__] = MOCK
exec(compile(SOURCE.replace("if not False:", "if not True:"), "<synthetic-no-authority>", "exec"), MOCK.__dict__)


def encoded(value):
    return "0x" + bytes(value).hex()


def wire(value):
    return json.dumps(value, separators=(",", ":")).encode()


class Fixture:
    def __init__(self, *, golden=False):
        from nacl.signing import SigningKey

        self.crypto = CryptoPrimitives()
        # Existing golden's public-only synthetic seed; never a real installed agent.
        key = SigningKey(bytes([66]) * 32) if golden else SigningKey.generate()
        self.signed, self.requests = [], []
        self.context_hook = None
        self.prepare_edit = None
        self.ack_edit = None
        self.after_admit = None
        self.lost_reply = False
        self.calls = {"prepare": 0, "admit": 0, "context": 0}
        self.policy = {
            "domain": {"chain_id": 421614, "settlement": [9] * 20},
            "market_id": [8] * 32 if golden else list(bytes.fromhex(self.crypto.digest(b"FairVenue.v2.market-id.v1\0BTCUSDC")[2:])),
            "config_version": "2", "epoch_ms": "100", "absolute_error_ms": "20",
            "max_disagreement_epochs": "1", "max_lifetime_epochs": "100",
            "max_oracle_age_ms": "1000", "max_oracle_future_ms": "0",
            "gateways": [{"gateway_id": index, "key_id": index,
                          "public_key": encoded(bytes(SigningKey(bytes([index]) * 32).verify_key)),
                          "first_epoch": "0", "end_epoch": str((1 << 64) - 1)}
                         for index in (1, 2, 3)],
        }
        self.grant = {
            "schema": "fairvenue-v1-agent-public-grant-v1", "chainId": "421614",
            "settlement": encoded([9] * 20), "owner": encoded([4] * 20),
            "account": "0x" + "0" * 63 + "7", "agentPublicKey": encoded(bytes(key.verify_key)),
            "permissions": 15, "delegationNonce": "1", "issuedAtMs": "0",
            "expiresAtMs": "40000", "maxExpiryMs": "40000", "canWithdraw": False,
        }
        self.context = {
            "authenticated": True, "session_scope": "03" * 32, "owner": self.grant["owner"],
            "prefix": copy.deepcopy(GOLDEN["intent"]["expected"]), "protocol_time_ms": "1000",
        }
        self.input = {"nonce": "1", "expiresAtMs": "20000", "action": {"type": "cancel", "order_id": "1"}}
        fixture = self

        class Signer:
            public_key = fixture.grant["agentPublicKey"]

            async def sign_bytes(self, data):
                fixture.signed.append(data)
                return bytes(key.sign(data).signature)

        self.signer = Signer()

    def build(self, value=None, context=None):
        return build_agent_command_signables(policy=self.policy, grant=self.grant,
                                             context=context or self.context, input=value or self.input,
                                             crypto=self.crypto)

    async def current_context(self):
        self.calls["context"] += 1
        if self.context_hook:
            await self.context_hook()
        return copy.deepcopy(self.context)

    async def post(self, path, body, timeout):
        assert 0 < timeout <= 15
        assert type(body) is bytes and len(body) <= 8192
        self.requests.append((path, body))
        request = json.loads(body)
        assert "account" not in request["intent"]
        expected = request["intent"]["expected"]
        context = {**copy.deepcopy(self.context), "prefix": copy.deepcopy(expected)}
        signables = self.build({"nonce": request["intent"]["nonce"],
                                "expiresAtMs": request["intent"]["expires_at_ms"],
                                "action": request["intent"]["action"]}, context)
        assert self.crypto.verify(request["intent_signature"], bytes.fromhex(signables["intent_signing_bytes"][2:]), self.grant["agentPublicKey"])
        if path == "/v1/agent/prepare":
            self.calls["prepare"] += 1
            result = {"status": "unsigned_agent_command", "prefix": expected,
                      "ordinary_signing_hash": signables["ordinary_signing_hash"],
                      "generation_signing_bytes": signables["generation_signing_bytes"],
                      "proof_backed": False, "l1_finality_verified": False, "public_release_go": False}
            if self.prepare_edit:
                self.prepare_edit(result)
            return wire(result)
        assert path == "/v1/agent/admit"
        assert self.crypto.verify(request["ordinary_signature"], bytes.fromhex(signables["ordinary_signing_hash"][2:]), self.grant["agentPublicKey"])
        assert self.crypto.verify(request["generation_signature"], bytes.fromhex(signables["generation_signing_bytes"][2:]), self.grant["agentPublicKey"])
        self.calls["admit"] += 1
        duplicate = self.context["prefix"]["head"]["count"] > expected["head"]["count"]
        self.context["prefix"]["head"] = {"count": expected["head"]["count"] + 1,
                                          "chain_hash": "0x" + "a" * 64,
                                          "public_data_hash": "0x" + "b" * 64,
                                          "next_root": "0x" + "c" * 64}
        if self.after_admit:
            self.after_admit()
        if self.lost_reply:
            self.lost_reply = False
            raise RuntimeError("Upstream private content must not escape")
        result = {"status": "soft_ack_prepared_not_final", "batch": 1, "generation": 1,
                  "step": expected["head"]["count"], "duplicate": duplicate, "outcome": "trade",
                  "roster": [1, 2], "head": copy.deepcopy(self.context["prefix"]["head"]),
                  "proof_backed": False, "l1_finality_verified": False, "public_release_go": False,
                  "anchor_receipt": {"opaque": "not claimed verified"}}
        if self.ack_edit:
            self.ack_edit(result)
        return wire(result)

    def client(self, cls=MOCK.AgentClient):
        return cls(policy=self.policy, grant=self.grant, signer=self.signer,
                   current_context=self.current_context, transport=self)


class ProtocolTests(unittest.TestCase):
    def test_all_six_existing_native_vectors_exact(self):
        fixture = Fixture(golden=True)
        result = fixture.build()
        self.assertEqual(result["intent"], GOLDEN["intent"])
        for field in ("intent_signing_bytes", "ordinary_signing_hash", "generation_signing_bytes"):
            self.assertEqual(result[field], GOLDEN[field])
        fixture.policy["market_id"] = list(bytes.fromhex(GOLDEN["operation_market_id"][2:]))
        actions = [*GOLDEN["operations"], {"action": GOLDEN["intent"]["action"]}]
        self.assertEqual({row["action"]["type"] for row in actions},
                         {"place", "modify", "cancel", "fee_mode", "leverage", "dead_man_switch"})
        for vector in GOLDEN["operations"]:
            result = fixture.build({**fixture.input, "action": vector["action"]})
            for field in ("intent_signing_bytes", "ordinary_signing_hash", "generation_signing_bytes"):
                self.assertEqual(result[field], vector[field])

    def test_float_unsafe_integer_and_noncanonical_decimal_refused(self):
        fixture = Fixture()
        for value in (1.0, True, "01", "-0", "1e3", str(1 << 53)):
            with self.subTest(value=value), self.assertRaises(AgentError):
                fixture.build({**fixture.input, "nonce": value})
        for deadline in ("00", "-0", 0):
            with self.assertRaises(AgentError):
                fixture.build({**fixture.input, "action": {"type": "dead_man_switch", "deadline_ms": deadline}})
        self.assertIn("intent", fixture.build({**fixture.input, "action": {"type": "dead_man_switch", "deadline_ms": "0"}}))
        order = copy.deepcopy(GOLDEN["operations"][0]["action"])
        for price in (60000.0, "0", "01.0", "1e2", "0." + "1" * 29, str(1 << 96)):
            order["order"]["price"] = price
            with self.assertRaises(AgentError):
                fixture.build({**fixture.input, "action": order})

    def test_domain_grant_permissions_keys_and_context_refused(self):
        cases = [
            ("policy", lambda value: value["domain"].update(chain_id=31337)),
            ("policy", lambda value: value["domain"].update(settlement=[10] * 20)),
            ("policy", lambda value: value["gateways"][1].update(public_key=value["gateways"][0]["public_key"])),
            ("grant", lambda value: value.update(canWithdraw=True)),
            ("grant", lambda value: value.update(permissions=1)),
            ("grant", lambda value: value.update(account="0x" + "f" * 64)),
            ("grant", lambda value: value.update(agentPublicKey="0x" + "01" + "00" * 31)),
            ("context", lambda value: value.update(owner="0x" + "05" * 20)),
            ("context", lambda value: value.update(protocol_time_ms="40000")),
            ("context", lambda value: value["prefix"]["head"].update(count=512)),
        ]
        for target, change in cases:
            fixture = Fixture()
            change(getattr(fixture, target))
            with self.subTest(target=target), self.assertRaises(AgentError):
                fixture.build()

    def test_fixed_crypto_strict_scalar_points_and_memory_signer(self):
        fixture = Fixture()
        self.assertFalse(fixture.crypto.strong("0x" + "00" * 32))
        self.assertFalse(fixture.crypto.strong("0x01" + "00" * 31))
        self.assertFalse(fixture.crypto.verify("0x" + "ff" * 64, b"message", fixture.grant["agentPublicKey"]))
        signer = MemorySigner()
        self.assertTrue(fixture.crypto.strong(signer.public_key))
        self.assertNotIn("SigningKey", repr(signer))

    def test_http_origin_and_json_bounds(self):
        for origin in ("https://v1.example", "http://127.0.0.1:8080", "http://[::1]:8080"):
            HttpTransport(origin)
        for origin in ("http://example.com", "https://user:secret@example.com", "https://example.com/",
                       "https://example.com?x=1", "https://example.com#secret", "https://example.com:0"):
            with self.assertRaises(AgentError):
                HttpTransport(origin)
        for body in (b'{"x":1,"x":2}', b'{"x":1.5}', b'{"x":NaN}', b"\xff", b" " * 16385):
            with self.assertRaises(AgentError):
                _json(body)


class KeyFileTests(unittest.TestCase):
    def exported(self):
        from nacl.signing import SigningKey

        fixture = Fixture()
        key = SigningKey.generate()  # Ephemeral generated secret, private temp file only.
        public_key = encoded(bytes(key.verify_key))
        grant = {**fixture.grant, "agentPublicKey": public_key}
        return fixture.policy, {
            "schema": "fairvenue-isolated-v1-agent-key-v1", "chain_id": 421614,
            "settlement": grant["settlement"], "agent_public_key": public_key,
            "public_grant": grant, "private_key": encoded(bytes(key)),
        }

    def write(self, directory, value, name="key.json", mode=0o600):
        path = str((Path(directory) / name).resolve())
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as file:
            file.write(value if type(value) is bytes else wire(value))
        return path

    def test_exact_owner_private_v1_export_and_no_secret_repr(self):
        policy, exported = self.exported()
        with tempfile.TemporaryDirectory(prefix="schema6-key-offline-") as directory:
            path = self.write(directory, exported)
            before = Path(path).read_bytes()
            signer, grant = load_v1_agent_key(path, policy=policy)
            self.assertEqual(signer.public_key, exported["agent_public_key"])
            self.assertEqual(grant, exported["public_grant"])
            self.assertNotIn(exported["private_key"][2:], repr(signer))
            self.assertEqual(Path(path).read_bytes(), before)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_unknown_legacy_duplicate_wrong_domain_key_and_grant_refused(self):
        edits = [
            lambda value: value.update(schema="arena-agent-key-v1"),
            lambda value: value.update(chain_id=31337),
            lambda value: value.update(settlement="0x" + "08" * 20),
            lambda value: value.update(agent_public_key="0x" + "ff" * 32),
            lambda value: value["public_grant"].update(agentPublicKey="0x" + "ff" * 32),
            lambda value: value.update(private_key="0x" + "00" * 31),
            lambda value: value.update(secret="must refuse extra field"),
        ]
        for edit in edits:
            policy, exported = self.exported()
            edit(exported)
            with tempfile.TemporaryDirectory(prefix="schema6-key-offline-") as directory:
                path = self.write(directory, exported)
                before = Path(path).read_bytes()
                with self.assertRaises(AgentError):
                    load_v1_agent_key(path, policy=policy)
                self.assertEqual(Path(path).read_bytes(), before)
        policy, exported = self.exported()
        with tempfile.TemporaryDirectory(prefix="schema6-key-offline-") as directory:
            duplicate = wire(exported)[:-1] + b',"chain_id":421614}'
            for index, raw in enumerate((duplicate, b"{bad", b" " * 16385)):
                path = self.write(directory, raw, f"invalid{index}.json")
                with self.assertRaises(AgentError):
                    load_v1_agent_key(path, policy=policy)

    def test_symlink_hardlink_fifo_and_permissions_refused_without_repair(self):
        policy, exported = self.exported()
        with tempfile.TemporaryDirectory(prefix="schema6-key-offline-") as directory:
            path = self.write(directory, exported)
            symlink = str(Path(directory).resolve() / "symlink.json")
            os.symlink(path, symlink)
            with self.assertRaises(AgentError):
                load_v1_agent_key(symlink, policy=policy)
            link = str(Path(directory).resolve() / "hardlink.json")
            os.link(path, link)
            with self.assertRaises(AgentError):
                load_v1_agent_key(path, policy=policy)
            os.unlink(link)
            os.chmod(path, 0o644)
            with self.assertRaises(AgentError):
                load_v1_agent_key(path, policy=policy)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            fifo = str(Path(directory).resolve() / "fifo.json")
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(AgentError):
                load_v1_agent_key(fifo, policy=policy)


class HttpDeadlineTests(unittest.IsolatedAsyncioTestCase):
    class Process:
        def __init__(self, *, output=None):
            self.returncode = None if output is None else 0
            self.dead = asyncio.Event()
            self.reading = asyncio.Event()
            self.writes = []
            self.kills = 0
            self.reaped = False
            self.output = output
            process = self

            class Input:
                def write(self, data):
                    process.writes.append(data)
                async def drain(self):
                    pass
                def close(self):
                    pass

            class Output:
                async def read(self, size):
                    process.reading.set()
                    if process.output is None:
                        await process.dead.wait()  # Simulated stuck DNS/header drip, no socket/network.
                        return b""
                    chunk, process.output = process.output[:size], process.output[size:]
                    return chunk

            self.stdin, self.stdout = Input(), Output()

        def kill(self):
            self.kills += 1
            self.returncode = -9
            self.dead.set()

        async def wait(self):
            if self.returncode is None:
                await self.dead.wait()
            await asyncio.sleep(0)
            self.reaped = True
            return self.returncode

    async def test_total_watchdog_kills_slow_connect_header_body_worker(self):
        for phase in ("DNS/connect", "slow header drip", "body stall"):
            process = self.Process()
            async def spawn(*args, **kwargs):
                self.assertEqual(args[0], sys.executable)
                self.assertEqual(args[1], "-I")
                self.assertEqual(Path(args[2]).resolve(), ROOT / "fairvenue_schema6/transport.py")
                self.assertEqual(kwargs["env"], {})
                self.assertEqual(kwargs["stderr"], asyncio.subprocess.DEVNULL)
                return process
            with self.subTest(phase=phase), patch("asyncio.create_subprocess_exec", side_effect=spawn):
                with self.assertRaises(AgentError) as raised:
                    await HttpTransport("https://v1.example").post("/v1/agent/admit", b'{"signed":"public"}', 0.01)
            self.assertEqual(raised.exception.code, "deadline")
            self.assertEqual(process.kills, 1)
            self.assertTrue(process.reaped)
            request = json.loads(process.writes[0])
            self.assertEqual(set(request), {"origin", "path", "body", "timeout"})
            self.assertNotIn("private", process.writes[0].decode())

    async def test_cancellation_during_spawn_and_headers_still_kills_and_reaps(self):
        for cancel_spawn in (False, True):
            process = self.Process()
            spawning, release = asyncio.Event(), asyncio.Event()
            async def spawn(*args, **kwargs):
                spawning.set()
                if cancel_spawn:
                    await release.wait()
                return process
            with patch("asyncio.create_subprocess_exec", side_effect=spawn):
                task = asyncio.create_task(HttpTransport("https://v1.example").post("/v1/agent/admit", b"{}", 0.1))
                await spawning.wait()
                if not cancel_spawn:
                    await process.reading.wait()
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                task.cancel()  # Cleanup must defer even another caller cancellation.
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(process.kills, 1)
            self.assertTrue(process.reaped)

    async def test_bounded_output_and_successful_public_pipe(self):
        for output in (b'{"public":true}', b"x" * 16385):
            process = self.Process(output=output)
            async def spawn(*args, **kwargs):
                return process
            with patch("asyncio.create_subprocess_exec", side_effect=spawn):
                if len(output) > 16384:
                    with self.assertRaises(AgentError):
                        await HttpTransport("https://v1.example").post("/v1/agent/prepare", b"{}", 0.1)
                else:
                    self.assertEqual(await HttpTransport("https://v1.example").post("/v1/agent/prepare", b"{}", 0.1), output)
            self.assertTrue(process.reaped)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_closed_before_dependencies_callbacks_or_input(self):
        class Signer:
            public_key = "not inspected"
            async def sign_bytes(self, _):
                self.fail("must not sign")
        class Transport:
            async def post(self, *_):
                self.fail("must not connect")
        async def context():
            self.fail("must not read session")
        client = AgentClient(policy={}, grant={}, signer=Signer(), current_context=context, transport=Transport())
        with patch.dict(os.environ, {"FAIRVENUE_SCHEMA6_ENABLE": "1"}), patch("fairvenue_schema6.client.CryptoPrimitives", side_effect=AssertionError("lazy")):
            client.SOURCE_ACTIVATION_ENABLED = True
            for operation in (client.run(object()), client.retry_admission()):
                with self.assertRaises(AgentError) as raised:
                    await operation
                self.assertEqual(raised.exception.code, "activation_closed")

    async def test_exact_signatures_ack_and_nonce_floor(self):
        fixture = Fixture()
        client = fixture.client()
        ack = await client.run(fixture.input)
        self.assertEqual(len(fixture.signed), 3)
        self.assertEqual([path for path, _ in fixture.requests], ["/v1/agent/prepare", "/v1/agent/admit"])
        self.assertNotIn("anchor_receipt", ack)
        self.assertFalse(ack["proof_backed"])
        self.assertFalse(client.has_pending_request())
        with self.assertRaises(AgentError) as raised:
            await fixture.client().run(fixture.input)
        self.assertEqual(raised.exception.code, "nonce_reused")

    async def test_wrong_prepare_signables_not_blindly_signed(self):
        fixture = Fixture()
        fixture.prepare_edit = lambda result: result.update(ordinary_signing_hash="0x" + "ff" * 32)
        with self.assertRaises(AgentError):
            await fixture.client().run(fixture.input)
        self.assertEqual(len(fixture.signed), 1)
        self.assertEqual(fixture.calls["admit"], 0)

    async def test_context_race_before_ordinary_signing(self):
        fixture = Fixture()
        async def hook():
            if fixture.calls["context"] == 3:
                fixture.context["session_scope"] = "04" * 32
        fixture.context_hook = hook
        with self.assertRaises(AgentError):
            await fixture.client().run(fixture.input)
        self.assertEqual(len(fixture.signed), 1)
        self.assertEqual(fixture.calls["admit"], 0)

    async def test_unknown_outcome_same_exact_retry_no_resign(self):
        fixture = Fixture()
        fixture.lost_reply = True
        client = fixture.client()
        with self.assertRaises(AgentError) as raised:
            await client.run(fixture.input)
        self.assertEqual(raised.exception.code, "outcome_unknown")
        self.assertNotIn("Upstream", str(raised.exception))
        self.assertTrue(client.has_pending_request())
        with self.assertRaises(AgentError):
            await fixture.client().run({**fixture.input, "nonce": "2"})
        ack = await fixture.client().retry_admission()
        self.assertTrue(ack["duplicate"])
        self.assertEqual(fixture.requests[-1], fixture.requests[-2])
        self.assertEqual(len(fixture.signed), 3)
        self.assertFalse(client.has_pending_request())

    async def test_invalid_ack_and_post_context_keep_pending(self):
        for edit in (lambda row: row.update(proof_backed=True), lambda row: row.update(roster=[1, 1]),
                     lambda row: row.update(batch=True)):
            fixture = Fixture()
            fixture.ack_edit = edit
            client = fixture.client()
            with self.assertRaises(AgentError):
                await client.run(fixture.input)
            self.assertTrue(client.has_pending_request())
        fixture = Fixture()
        fixture.after_admit = lambda: fixture.context.update(owner="0x" + "05" * 20)
        client = fixture.client()
        with self.assertRaises(AgentError):
            await client.run(fixture.input)
        self.assertTrue(client.has_pending_request())

    async def test_retry_wrong_opening_or_expired_grant_never_sends(self):
        for edit in (lambda row: row["prefix"]["opening"].update(generation=2),
                     lambda row: row.update(protocol_time_ms="40000"),
                     lambda row: row["prefix"]["domain"].update(chain_id=31337)):
            fixture = Fixture()
            fixture.lost_reply = True
            client = fixture.client()
            with self.assertRaises(AgentError):
                await client.run(fixture.input)
            edit(fixture.context)
            count = len(fixture.requests)
            with self.assertRaises(AgentError):
                await client.retry_admission()
            self.assertEqual(len(fixture.requests), count)
            self.assertTrue(client.has_pending_request())

    async def test_timeout_and_cancellation_retain_inflight_busy_permit(self):
        for cancel in (False, True):
            fixture = Fixture()
            entered, release = asyncio.Event(), asyncio.Event()
            async def hook():
                entered.set()
                await release.wait()
            fixture.context_hook = hook
            client = fixture.client()
            task = asyncio.create_task(client.run(fixture.input, timeout_ms=20))
            await entered.wait()
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                with self.assertRaises(AgentError) as raised:
                    await task
                self.assertEqual(raised.exception.code, "deadline")
            with self.assertRaises(AgentError) as raised:
                await fixture.client().run(fixture.input)
            self.assertEqual(raised.exception.code, "busy")
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            fixture.context_hook = None
            self.assertEqual(fixture.calls["admit"], 0)

    async def test_wall_clock_rollback_cannot_extend_monotonic_deadline(self):
        fixture = Fixture()
        async def hook():
            await asyncio.sleep(0.04)
        fixture.context_hook = hook
        with patch("time.time", return_value=0):
            with self.assertRaises(AgentError) as raised:
                await fixture.client().run(fixture.input, timeout_ms=10)
        self.assertEqual(raised.exception.code, "deadline")
        await asyncio.sleep(0.05)
        self.assertEqual(fixture.calls["admit"], 0)

    async def test_pins_frozen_same_signer_conflicts_and_wrong_signature(self):
        fixture = Fixture()
        client = fixture.client()
        fixture.policy["domain"]["chain_id"] = 31337
        with self.assertRaises(AgentError):
            fixture.client()
        fixture.policy["domain"]["chain_id"] = 421614
        await client.run(fixture.input)
        fixture = Fixture()
        async def bad_sign(_):
            return b"\x00" * 64
        fixture.signer.sign_bytes = bad_sign
        with self.assertRaises(AgentError) as raised:
            await fixture.client().run(fixture.input)
        self.assertEqual(raised.exception.code, "signature_invalid")
        self.assertEqual(fixture.calls["prepare"], 0)


if __name__ == "__main__":
    unittest.main()
