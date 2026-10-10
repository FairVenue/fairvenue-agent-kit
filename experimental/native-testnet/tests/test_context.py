"""Offline synthetic bootstrap: public native vector parity, not hosted/proof evidence."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from fairvenue_schema6 import AgentError, BotContextProvider, build_agent_context_signing_bytes
from fairvenue_schema6.context import _scope
from fairvenue_schema6.protocol import context_copy, grant_copy, policy_copy
from test_agent import Fixture, MOCK as COMMAND_MOCK, wire

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/agent-context.golden.json").read_text())
SOURCE = (ROOT / "fairvenue_schema6/context.py").read_text()
assert SOURCE.count("if not False:") == 1
MOCK = types.ModuleType("fairvenue_schema6._synthetic_context")
MOCK.__package__ = "fairvenue_schema6"
sys.modules[MOCK.__name__] = MOCK
# Only ephemeral in-memory evaluation. No activated file/CLI/env/request unlock.
exec(compile(SOURCE.replace("if not False:", "if not True:"),
             "<synthetic-read-only-no-authority>", "exec"), MOCK.__dict__)


class ContextFixture(Fixture):
    def __init__(self):
        super().__init__(golden=True)
        self.edit = None
        self.context_wait = None
        self.challenges = []
        self.next_nonce = "1"
        self.context["session_scope"] = _scope(
            policy_copy(self.policy, self.crypto),
            grant_copy(self.grant, policy_copy(self.policy, self.crypto), self.crypto),
        )

    async def post(self, path, body, timeout):
        if path != "/v1/agent/context":
            result = await super().post(path, body, timeout)
            if path == "/v1/agent/admit":
                self.next_nonce = str(int(json.loads(body)["intent"]["nonce"]) + 1)
            return result
        self.assert_read_request(body, timeout)
        request = json.loads(body)
        self.challenges.append(request["challenge"])
        if self.context_wait:
            await self.context_wait()
        response = {
            "schema": "fairvenue-v1-agent-context-v1",
            "status": "canonical_prepared_agent_context_not_final",
            "challenge": request["challenge"], "context": copy.deepcopy(self.context),
            "public_grant": copy.deepcopy(self.grant), "next_nonce": self.next_nonce,
            "proof_backed": False, "l1_finality_verified": False, "public_release_go": False,
        }
        if self.edit:
            self.edit(response)
        return wire(response)

    def assert_read_request(self, body, timeout):
        assert 0 < timeout <= 15
        request = json.loads(body)
        assert set(request) == {"schema", "chain_id", "settlement", "agent_public_key", "challenge", "signature"}
        assert request["schema"] == "fairvenue-v1-agent-context-request-v1"
        assert request["chain_id"] == 421614 and request["settlement"] == self.grant["settlement"]
        assert request["agent_public_key"] == self.grant["agentPublicKey"]
        message = build_agent_context_signing_bytes(
            policy=self.policy, agent_public_key=request["agent_public_key"], challenge=request["challenge"]
        )
        assert self.crypto.verify(request["signature"], message, self.grant["agentPublicKey"])
        assert b"private_key" not in body and b"session" not in body and b"account" not in body

    def provider(self, cls=MOCK.BotContextProvider):
        return cls(policy=self.policy, grant=self.grant, signer=self.signer, transport=self)


class ContextProtocolTests(unittest.TestCase):
    def test_native_golden_signing_bytes_and_public_scope(self):
        fixture = ContextFixture()
        self.assertEqual(fixture.policy, GOLDEN["policy"])
        self.assertEqual(fixture.grant, GOLDEN["public_grant"])
        self.assertEqual("0x" + build_agent_context_signing_bytes(
            policy=fixture.policy, agent_public_key=fixture.grant["agentPublicKey"],
            challenge=GOLDEN["request"]["challenge"]
        ).hex(), GOLDEN["signing_bytes"])
        self.assertEqual(fixture.context["session_scope"], GOLDEN["session_scope"])
        self.assertEqual(policy_copy(fixture.policy, fixture.crypto)["commitment"], GOLDEN["policy_hash"])
        self.assertTrue(fixture.crypto.verify(
            GOLDEN["request"]["signature"], bytes.fromhex(GOLDEN["signing_bytes"][2:]),
            fixture.grant["agentPublicKey"]
        ))
        native = GOLDEN["response"]
        pinned = policy_copy(fixture.policy, fixture.crypto)
        self.assertEqual(context_copy(native["context"], pinned, fixture.grant), fixture.context)
        self.assertEqual(grant_copy(native["public_grant"], pinned, fixture.crypto), fixture.grant)
        self.assertEqual(native["next_nonce"], "1")
        self.assertEqual(native["challenge"], GOLDEN["request"]["challenge"])

    def test_small_order_key_bad_domain_and_challenge_refuse(self):
        fixture = ContextFixture()
        cases = [
            (fixture.policy, "0x01" + "00" * 31, GOLDEN["request"]["challenge"]),
            ({**fixture.policy, "domain": {"chain_id": 1, "settlement": [9] * 20}},
             fixture.grant["agentPublicKey"], GOLDEN["request"]["challenge"]),
            (fixture.policy, fixture.grant["agentPublicKey"], "0x" + "00" * 32),
        ]
        for policy, key, challenge in cases:
            with self.assertRaises(AgentError):
                build_agent_context_signing_bytes(policy=policy, agent_public_key=key, challenge=challenge)


class ContextClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_before_challenge_sign_or_transport(self):
        fixture = ContextFixture()
        provider = fixture.provider(BotContextProvider)
        with patch("fairvenue_schema6.context.secrets.token_bytes", side_effect=AssertionError("must not read")):
            with self.assertRaises(AgentError) as caught:
                await provider.snapshot(timeout_ms=object())
        self.assertEqual(caught.exception.code, "activation_closed")
        self.assertEqual(fixture.signed, [])
        self.assertEqual(fixture.challenges, [])

    async def test_fresh_challenge_exact_grant_and_immutable_public_copy(self):
        fixture = ContextFixture()
        provider = fixture.provider()
        snapshot = await provider.snapshot()
        self.assertEqual(snapshot["next_nonce"], "1")
        self.assertEqual(snapshot["context"], fixture.context)
        snapshot["context"]["owner"] = "0x" + "05" * 20
        self.assertEqual(await provider.current_context(), fixture.context)
        self.assertEqual(len(fixture.challenges), 2)
        self.assertNotEqual(*fixture.challenges)

    async def test_concrete_provider_drives_existing_signed_agent_client_without_cookie_hook(self):
        fixture = ContextFixture()
        provider = fixture.provider()
        snapshot = await provider.snapshot()
        client = COMMAND_MOCK.AgentClient(
            policy=fixture.policy, grant=fixture.grant, signer=fixture.signer,
            current_context=provider.current_context, transport=fixture,
        )
        ack = await client.run({**fixture.input, "nonce": snapshot["next_nonce"]})
        self.assertEqual(ack["outcome"], "trade")
        self.assertFalse(client.has_pending_request())
        self.assertEqual((await provider.snapshot())["next_nonce"], "2")
        self.assertGreater(len(fixture.challenges), 3)
        self.assertEqual(fixture.calls["admit"], 1)

    async def test_response_cannot_nominate_owner_grant_scope_domain_nonce_or_proof(self):
        edits = [
            lambda value: value.update(challenge="0x" + "44" * 32),
            lambda value: value.update(schema="other"),
            lambda value: value.update(status="verified"),
            lambda value: value.update(private_key="never acceptable"),
            lambda value: value.update(proof_backed=True),
            lambda value: value.update(l1_finality_verified=True),
            lambda value: value.update(public_release_go=True),
            lambda value: value.update(next_nonce="0"),
            lambda value: value.update(next_nonce="01"),
            lambda value: value.update(next_nonce=1),
            lambda value: value.update(next_nonce=str(1 << 53)),
            lambda value: value["context"].update(session_scope="33" * 32),
            lambda value: value["context"].update(protocol_time_ms="40000"),
            lambda value: value["context"].update(protocol_time_ms=1000),
            lambda value: value["context"].update(owner="0x" + "05" * 20),
            lambda value: value["public_grant"].update(account="0x" + "0" * 63 + "8"),
            lambda value: value["public_grant"].update(permissions=1),
            lambda value: value["public_grant"].update(delegationNonce="2"),
            lambda value: value["public_grant"].update(canWithdraw=True),
            lambda value: value["context"]["prefix"]["domain"].update(settlement=[10] * 20),
        ]
        for edit in edits:
            fixture = ContextFixture()
            fixture.edit = edit
            with self.assertRaises(AgentError):
                await fixture.provider().snapshot()

    async def test_time_and_nonce_rewind_refuse(self):
        fixture = ContextFixture()
        fixture.edit = lambda value: value.update(next_nonce="2")
        provider = fixture.provider()
        await provider.snapshot()
        fixture.edit = None
        with self.assertRaises(AgentError):
            await provider.snapshot()
        fixture.edit = lambda value: value.update(next_nonce="2")
        fixture.context["protocol_time_ms"] = "999"
        with self.assertRaises(AgentError):
            await provider.snapshot()

    async def test_timeout_keeps_unfinished_callback_busy_and_sanitizes_errors(self):
        fixture = ContextFixture()
        started, release = asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await release.wait()

        fixture.context_wait = slow
        provider = fixture.provider()
        with self.assertRaises(AgentError) as caught:
            await provider.snapshot(timeout_ms=15)
        self.assertEqual(caught.exception.code, "deadline")
        self.assertTrue(started.is_set())
        with self.assertRaises(AgentError) as caught:
            await provider.snapshot()
        self.assertEqual(caught.exception.code, "busy")
        release.set()
        await asyncio.sleep(0.01)
        fixture.context_wait = None
        await provider.snapshot()

        async def error():
            raise RuntimeError("secret request and session must never escape")

        fixture.context_wait = error
        with self.assertRaises(AgentError) as caught:
            await provider.snapshot()
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertNotIn("secret", str(caught.exception))

    async def test_cancel_keeps_exact_callback_permit_until_completion(self):
        fixture = ContextFixture()
        started, release = asyncio.Event(), asyncio.Event()

        async def wait():
            started.set()
            await release.wait()

        fixture.context_wait = wait
        provider = fixture.provider()
        task = asyncio.create_task(provider.snapshot())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(AgentError) as caught:
            await provider.snapshot()
        self.assertEqual(caught.exception.code, "busy")
        release.set()
        await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
