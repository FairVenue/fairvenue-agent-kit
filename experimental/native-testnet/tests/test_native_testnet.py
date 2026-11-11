"""Synthetic TS2 HTTP/continuity fixtures; no funded/live/proof acceptance. TEST keys only."""

import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fairvenue_schema6 import AgentError, CryptoPrimitives, NativeTestnetClient, ts2
from fairvenue_schema6 import native_testnet as native
from fairvenue_schema6.crypto import MemorySigner
from fairvenue_schema6.protocol import AdmitRefusal
from fairvenue_schema6.transport import HttpTransport

API = "https://native-testnet.example/"
SEED = "0x" + "42" * 32  # Public synthetic TEST seed, never a real key.
OWNER_SIGNATURE = "0x" + "5a" * 64 + "1b"  # Synthetic. Only the server recovers it.
ACCOUNT_KEY = "0x" + "00" * 31 + "07"


def body(value):
    return json.dumps(value, separators=(",", ":")).encode()


def not_placed(request, reason, **changes):
    """The server's exact definitive refusal body for ``request``."""
    return body({"error": "not_placed", "reason": reason,
                 "request_sha256": ts2.request_sha256(request), "retry": "resign",
                 "proof_backed": False, "l1_finality_verified": False,
                 "public_release_go": False, **changes})


def refusal_status(reason):
    return 429 if reason == "rate_limited" else 409


def market(fixture, **changes):
    """One public ``fairvenue-ts2-public-market-v1`` snapshot of the fixture."""
    value = {
        "schema": ts2.MARKET_SCHEMA, "environment": "TESTNET", **ts2.PINS, "symbol": "BTCUSDC",
        "mark_price": "80000.05", "mark_observed_at_ms": fixture.context["protocol_time_ms"],
        "protocol_time_ms": fixture.context["protocol_time_ms"],
        "batch": {"open_batch_number": "7", "lineage": "1"},
        "admission": {"open": True, "reasons": []},
        "fee_schedule": {"fixed_maker_rate": "0.00003", "fixed_taker_rate": "0.00012",
                         "pnl_base_rate": "0.02", "pnl_taker_surcharge_rate": "0.04"},
        "market_rules": {"tick_size": "0.1", "step_size": "0.001", "fairlag_ms": "100",
                         "default_leverage": "10"},
        "bids": [{"price": "79999.9", "quantity": "0.02"}, {"price": "79990", "quantity": "1"}],
        "asks": [{"price": "80000.2", "quantity": "0.01"}],
        "trades": [{"trade_id": "9", "price": "80000.1", "quantity": "0.01", "side": "BUY",
                    "timestamp_ms": fixture.context["protocol_time_ms"], "batch_number": "7",
                    "lineage": "1", "settlement": {"state": "operator_soft"}}],
        "source": "operator_local_native_soft_snapshot", "proof_backed": False,
        "l1_finality_verified": False, "public_release_go": False,
    }
    value.update(changes)
    return value


def view(fixture, **changes):
    """One ``/v1/ts2/agent/read`` reply exactly as ``views.rs project`` and
    ``Gateway::read`` build it: the view plus observed time, readiness and the
    trading flag."""
    now = fixture.context["protocol_time_ms"]
    later = str(int(now) + 3_600_000)
    chain = {"state": "controller_observed_settled_final", "state_root": "0x" + "11" * 32,
             "transaction_hash": "0x" + "22" * 32, "block_hash": "0x" + "33" * 32,
             "block_number": "100", "finalized_at_block": "110"}
    value = {
        "schema": ts2.VIEW_SCHEMA, **ts2.PINS,
        "source": "operator_local_native_and_controller_observed_chain", "observed_at_ms": now,
        "market": {"symbol": "BTCUSDC", "tick_size": "0.1", "step_size": "0.001",
                   "fairlag_ms": "100",
                   "book": {"source": "native_matcher", "snapshot": True, "depth": 20,
                            "bids": [{"price": "79999.9", "quantity": "0.02"}],
                            "asks": [{"price": "80000.2", "quantity": "0.01"}]},
                   "mark": {"price_micros": "80000050000", "observed_at_ms": now}},
        "account": {
            "soft": {"wallet_balance": "1000.5", "reserved_order_margin": "8",
                     "locked_position_margin": "16.0005", "realized_pnl": "-1.25",
                     "funding_paid": "0.01", "position_quantity": "-0.002",
                     "position_entry_price": "80010"},
            "settled": {"batch_number": "6", "lineage": "1", "state_root": "0x" + "11" * 32,
                        "transaction_hash": "0x" + "22" * 32, "block_hash": "0x" + "33" * 32,
                        "block_number": "100", "finalized_at_block": None,
                        "wallet_balance": "1000", "realized_pnl": "0", "funding_paid": "0",
                        "position_quantity": "0", "exit_micros": "1000000000"},
            "settled_final": None},
        "orders": [
            {"order_id": "0x" + "aa" * 32, "status": "partially_filled", "lineage": "1",
             "admitted_at_ms": now,
             "detail": {"side": "buy", "kind": "limit", "time_in_force": "GTC",
                        "limit_price_ticks": "799900", "quantity_lots": "3", "filled_lots": "1",
                        "expires_at_ms": later, "post_only": True, "reduce_only": False}},
            # FairLag-held: never its side, price or size.
            {"order_id": "0x" + "bb" * 32, "status": "pending", "lineage": "1",
             "admitted_at_ms": now},
            {"order_id": "0x" + "cc" * 32, "status": "filled", "lineage": "1",
             "admitted_at_ms": now,
             "detail": {"side": "sell", "kind": "market", "time_in_force": "IOC",
                        "limit_price_ticks": "796000", "quantity_lots": "2", "filled_lots": "2",
                        "expires_at_ms": later, "post_only": False, "reduce_only": False}}],
        "fills": [
            {"position": "14", "at_ms": now, "batch_number": "0", "lineage": "1",
             "kind": "fill", "role": "taker", "side": "sell", "order_id": "0x" + "cc" * 32,
             "price_ticks": "800002", "quantity_lots": "2", "fee": "0.0192",
             "settlement": {"state": "operator_soft"}},
            {"position": "12", "at_ms": now, "batch_number": "6", "lineage": "1",
             "kind": "fill", "role": "maker", "side": "buy", "order_id": "0x" + "aa" * 32,
             "price_ticks": "799900", "quantity_lots": "1", "fee": "-0.0024",
             "settlement": chain}],
        "history": {"order_count": "3", "fill_count": "2", "row_limit": 64,
                    "orders_truncated": False, "fills_truncated": False},
        "admission": "open", "tradingEnabled": True, "proofVerified": False,
        "publicFinality": False, "publicReleaseGo": False,
        "readiness": {"schema": ts2.READINESS_SCHEMA,
                      "source": "operator_single_provider_rpc_and_native_writer", **ts2.PINS,
                      "enabled": True, "reason": "open", "observed_at_ms": now,
                      "chain_block_number": "5", "chain_block_hash": "0x" + "44" * 32,
                      "chain_block_time_ms": str(int(now) - 2_000),
                      "captured_at_ms": str(int(now) - 1_000),
                      "expires_at_ms": str(int(now) + 14_400_000)},
    }
    value.update(changes)
    return value


class NativeFixture:
    """A synthetic TS2 participant service: context, public market, admit, recovery."""

    def __init__(self, seed=SEED, nonce="1", permissions=11):
        self.crypto = CryptoPrimitives()
        self.signer = MemorySigner._from_v1_seed(bytes.fromhex(seed[2:]))
        self.seed = seed
        now = int(time.time() * 1000)
        self.grant = {"material": {
            "schema": ts2.MATERIAL_SCHEMA, **ts2.PINS, "owner": "0x" + "04" * 20,
            "account_key": ACCOUNT_KEY, "agent_public_key": self.signer.public_key,
            "permissions": permissions, "nonce": nonce, "issued_at_ms": str(now - 60_000),
            "expires_at_ms": str(now + 86_400_000), "max_expiry_ms": str(now + 86_400_000),
            "can_withdraw": False}, "signature": OWNER_SIGNATURE}
        self.policy = copy.deepcopy(ts2.PINS)
        self.context = {**ts2.PINS, "account_key": ACCOUNT_KEY, "batch_number": "7",
                        "lineage": "1", "protocol_time_ms": str(now), "fee_mode": 0,
                        "leverage": "10", "account_settings_enabled": True,
                        "admission": {"open": True, "reasons": []}}
        self.command_nonce = 0  # The last applied security command nonce.
        self.journal = {}  # request_sha256 -> exact request
        self.calls = {"context": 0, "info": 0, "admit": 0, "recovery": 0, "read": 0}
        self.requests, self.signed = [], []
        self.lost_reply = False
        self.refusal = None  # request -> (status, body); status None is an unknown failure.
        self.recovery_status = None  # Overrides the journal-derived status.
        # A not_placed lookup always names its reason (service.rs
        # validate_ts2_agent_reply): rejected or expired_never_admitted.
        self.recovery_reason = "rejected"
        self.recovery_edit = self.context_edit = self.ack_edit = self.view_edit = None
        self.market = market(self)
        self.view = view(self)

    def export(self, **changes):
        """The browser's TS2 bot key download for this fixture's key."""
        return {"schema": ts2.KEY_SCHEMA, "environment": "TESTNET", **ts2.PINS, "api_url": API,
                "delegation": copy.deepcopy(self.grant), "private_key_seed": self.seed, **changes}

    def ack(self, request, digest, duplicate):
        action = request["action"]
        kind = action["type"]
        reply = {"schema": ts2.ACK_SCHEMA, "request_sha256": digest,
                 "config_hash": ts2.CONFIG_HASH, "status": "operator_journaled",
                 "proofVerified": False, "publicFinality": False, "publicReleaseGo": False,
                 "action": kind, "duplicate": duplicate}
        position = str(len(self.journal))
        if kind == "order":
            _, order_id = ts2.signing_message(ts2.PINS, request["account_key"],
                                              request["agent_public_key"], action, self.crypto)
            reply.update(order_id=order_id, position=position, held=False,
                         receipt={"digest": "0x" + "ab" * 32, "signature": "0x" + "cd" * 65},
                         certificates=[])
        elif kind in ("cancel", "kill_all"):
            # Fill-only proving (season 2): the matcher and journal enforce the
            # cancel, so no kill receipt is signed and kill_seq is 0.
            reply["receipt"] = {"digest": None, "signature": None, "kill_seq": "0",
                                "position": position,
                                "order_id": action.get("order_id", "0x" + "00" * 32)}
        else:
            reply["position"] = position
        return reply

    async def post(self, path, raw, timeout):
        assert type(raw) is bytes and len(raw) <= 8192 and 0 < timeout <= 15
        self.requests.append((path, raw))
        value = json.loads(raw)
        if path == "/v1/agent/context":
            self.calls["context"] += 1
            assert set(value) == {"schema", "chain_id", "settlement", "agent_public_key",
                                  "challenge", "signature"}
            assert value["schema"] == ts2.CONTEXT_REQUEST_SCHEMA
            message = ts2.context_signing_bytes(ts2.PINS, value["agent_public_key"],
                                                value["challenge"])
            assert self.crypto.verify(value["signature"], message, self.signer.public_key)
            self.signed.append(value["signature"])
            reply = {"schema": ts2.CONTEXT_SCHEMA,
                     "status": "operator_native_agent_context_not_final",
                     "challenge": value["challenge"], "context": copy.deepcopy(self.context),
                     "public_grant": copy.deepcopy(self.grant),
                     "next_command_nonce": str(self.command_nonce + 1), "proof_backed": False,
                     "l1_finality_verified": False, "public_release_go": False}
            if self.context_edit:
                self.context_edit(reply)
            return body(reply)
        if path == "/api/v1/info":
            self.calls["info"] += 1
            assert value == {"type": "native_market"}
            return body(self.market)
        if path == "/v1/ts2/agent/read":
            # read_auth.rs: kind 1, zero target, expiry in (now, now + 60 s], no nonce.
            self.calls["read"] += 1
            assert set(value) == {"schema", "chain_id", "settlement", "config_hash",
                                  "account_key", "agent_public_key", "expires_at_ms",
                                  "signature"}
            assert value["schema"] == ts2.READ_SCHEMA
            assert {key: value[key] for key in ts2.PINS} == ts2.PINS
            assert value["account_key"] == self.grant["material"]["account_key"]
            now = int(time.time() * 1000)
            assert now < int(value["expires_at_ms"]) <= now + 60_000
            message = (b"FV_TS2_READ_SIG\0" + bytes.fromhex(ts2.CONFIG_HASH[2:]) + b"\x01"
                       + (7).to_bytes(8, "big") + bytes(32)
                       + int(value["expires_at_ms"]).to_bytes(8, "big"))
            assert self.crypto.verify(value["signature"], message, self.signer.public_key)
            self.signed.append(value["signature"])
            reply = copy.deepcopy(self.view)
            if self.view_edit:
                self.view_edit(reply)
            return body(reply)
        request = ts2.request_copy(value, ts2.PINS, self.crypto)  # Exact signature check.
        digest = ts2.request_sha256(request)
        if path == "/v1/agent/admit":
            self.calls["admit"] += 1
            self.signed.append(request["signature"])
            if self.refusal:
                status, reply = self.refusal(request)
                if status is None:
                    raise AgentError("unavailable")  # A 503 or a transport error.
                raise AdmitRefusal(status, reply)
            duplicate = digest in self.journal
            self.journal[digest] = request
            if request["action"]["type"] in ts2.SECURITY:
                self.command_nonce = max(self.command_nonce,
                                         int(request["action"]["command_nonce"]))
            if self.lost_reply:
                raise AgentError("unavailable")  # Journaled, but the reply was lost.
            reply = self.ack(request, digest, duplicate)
            if self.ack_edit:
                self.ack_edit(reply)
            return body(reply)
        assert path == "/v1/agent/recovery"
        self.calls["recovery"] += 1
        status = self.recovery_status or (
            "operator_journaled" if digest in self.journal else "unknown")
        reply = {"schema": ts2.RECOVERY_SCHEMA, "request_sha256": digest, "status": status,
                 "action": request["action"]["type"], "lineage": "1", "retry_authorized": False,
                 "proofVerified": False, "publicFinality": False, "publicReleaseGo": False}
        if status == "not_placed":
            reply["reason"] = self.recovery_reason
        if self.recovery_edit:
            self.recovery_edit(reply)
        return body(reply)

    def client(self, directory, initialize=True, signer=None):
        return NativeTestnetClient(policy=self.policy, grant=self.grant,
                                   signer=signer or self.signer, transport=self,
                                   approved_api_url=API,
                                   custody_path=str(Path(directory).resolve() / "original.sqlite"),
                                   initialize=initialize)

    def expiry(self, lifetime=30_000):
        return str(int(time.time() * 1000) + lifetime)

    def order(self, **changes):
        order = {"symbol": "BTCUSDC", "side": "BUY", "order_type": "LIMIT",
                 "time_in_force": "GTC", "price": "79990", "quantity": "0.001",
                 "reduce_only": False, "post_only": False, **changes}
        return {"type": "place", "order": order}

    def admitted(self):
        return [json.loads(raw) for path, raw in self.requests if path == "/v1/agent/admit"]


class NativeTestnetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = NativeFixture()
        self.directory = tempfile.mkdtemp(prefix="ts2-native-fixture-")
        os.chmod(self.directory, 0o700)
        self.client = self.fixture.client(self.directory)

    async def asyncTearDown(self):
        try:
            self.client.close()
        except AgentError:
            pass
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    async def run_action(self, action, **changes):
        snapshot = await self.client.snapshot()
        return await self.client.run({"nonce": snapshot["next_nonce"],
                                      "expiresAtMs": self.fixture.expiry(), "action": action,
                                      **changes})

    async def test_signed_context_is_read_only_and_bound_to_this_key(self):
        snapshot = await self.client.snapshot()
        self.assertEqual(snapshot["next_nonce"], "1")
        self.assertEqual(snapshot["context"]["fee_mode"], 0)
        self.assertEqual(snapshot["context"]["admission"], {"open": True, "reasons": []})
        self.assertEqual(snapshot["public_grant"], self.fixture.grant)
        self.assertEqual(self.fixture.calls["admit"], 0)
        # Closed admission names one readiness reason, for example the funding hold.
        for reason in ("funding_owed", "chain_observation_expired", "oracle_stale"):
            self.fixture.context["admission"] = {"open": False, "reasons": [reason]}
            closed = (await self.client.snapshot())["context"]["admission"]
            self.assertEqual(closed, {"open": False, "reasons": [reason]})
        self.fixture.context["admission"] = {"open": True, "reasons": []}
        for edit in (lambda r: r.update(challenge="0x" + "11" * 32),
                     lambda r: r["public_grant"]["material"].update(nonce="2"),
                     lambda r: r["context"].update(config_hash="0x" + "11" * 32),
                     lambda r: r["context"].update(account_key="0x" + "00" * 31 + "08"),
                     lambda r: r["context"].update(fee_mode=2),
                     lambda r: r["context"]["admission"].update(open=False),
                     lambda r: r.update(proof_backed=True),
                     lambda r: r.update(private_key_seed=SEED)):
            self.fixture.context_edit = edit
            with self.assertRaises(AgentError) as caught:
                await self.client.snapshot()
            self.assertEqual(caught.exception.code, "response_invalid")

    async def test_limit_order_signs_once_and_its_original_resolves_by_exact_lookup(self):
        result = await self.run_action(self.fixture.order(price="79990.1", quantity="0.002"))
        ack, recovery = result["ack"], result["recovery"]
        self.assertEqual(recovery["status"], "accepted")
        self.assertEqual(recovery["wire_status"], "operator_journaled")
        self.assertFalse(self.client.has_pending_request())
        admitted = self.fixture.admitted()
        self.assertEqual(len(admitted), 1)
        action = admitted[0]["action"]
        self.assertEqual((action["limit_price_ticks"], action["quantity_lots"]), ("799901", "2"))
        self.assertEqual((action["side"], action["kind"], action["time_in_force"]), (0, 0, 0))
        self.assertEqual(action["fee_mode"], 0)
        self.assertEqual(action["delegation_nonce"], self.fixture.grant["material"]["nonce"])
        _, order_id = ts2.signing_message(ts2.PINS, ACCOUNT_KEY, self.fixture.signer.public_key,
                                          action, self.fixture.crypto)
        self.assertEqual(ack["order_id"], order_id)
        self.assertEqual(ack["request_sha256"], ts2.request_sha256(admitted[0]))
        self.assertEqual(self.fixture.calls,
                         {"context": 2, "info": 1, "admit": 1, "recovery": 1, "read": 0})
        self.assertNotIn(SEED[2:], Path(self.directory, "original.sqlite").read_bytes().hex())
        # Market rules are cached: the next order needs no second public read.
        await self.run_action(self.fixture.order(side="SELL", price="80010"))
        self.assertEqual(self.fixture.calls["info"], 1)
        # Each order gets its own random client nonce, so equal orders never collide.
        first, second = (await self.run_action(self.fixture.order()),
                         await self.run_action(self.fixture.order()))
        self.assertNotEqual(first["ack"]["order_id"], second["ack"]["order_id"])
        self.assertFalse(second["ack"]["duplicate"])

    async def test_off_tick_off_lot_and_unsupported_actions_refuse_before_signing(self):
        cases = [
            (self.fixture.order(price="79990.05"), "invalid_order"),
            (self.fixture.order(quantity="0.0015"), "invalid_order"),
            (self.fixture.order(price="1e5"), "invalid_order"),
            (self.fixture.order(order_type="MARKET", time_in_force="GTC", price=None), "invalid_order"),
            (self.fixture.order(post_only=True, time_in_force="IOC"), "invalid_order"),
            (self.fixture.order(price=None), "invalid_order"),
            ({"type": "modify", "order_id": "1", "replacement": {}}, "unsupported"),
            ({"type": "cancel", "order_id": "42"}, "invalid_input"),
            ({"type": "leverage", "leverage": "101"}, "invalid_input"),
            ({"type": "fee_mode", "mode": "MAKER"}, "invalid_input"),
        ]
        for action, code in cases:
            with self.subTest(action=action), self.assertRaises(AgentError) as caught:
                await self.run_action(action)
            self.assertEqual(caught.exception.code, code)
        self.assertIn("Cancel the order and place a new one",
                      str(AgentError("unsupported")))
        self.assertEqual(self.fixture.calls["admit"], 0)
        self.assertFalse(self.client.has_pending_request())

    async def test_market_orders_sign_a_half_percent_bound_away_from_the_book(self):
        await self.run_action(self.fixture.order(order_type="MARKET", time_in_force="IOC",
                                                 price=None))
        await self.run_action(self.fixture.order(order_type="MARKET", time_in_force="FOK",
                                                 side="SELL", price=None))
        await self.run_action(self.fixture.order(order_type="MARKET", time_in_force="IOC",
                                                 price="80400.01"))
        buy, sell, chosen = (request["action"] for request in self.fixture.admitted())
        # Best ask 80000.2 * 1.005 = 80400.201 -> up to 80400.3. Best bid
        # 79999.9 * 0.995 = 79599.9005 -> down to 79599.9. A chosen bound rounds up.
        self.assertEqual((buy["kind"], buy["time_in_force"], buy["limit_price_ticks"]),
                         (1, 1, "804003"))
        self.assertEqual((sell["side"], sell["time_in_force"], sell["limit_price_ticks"]),
                         (1, 2, "795999"))
        self.assertEqual(chosen["limit_price_ticks"], "804001")
        # A computed bound reads fresh best prices. A chosen bound needs only the tick.
        self.assertEqual(self.fixture.calls["info"], 2)
        # No ask and no mark: nothing to bound a BUY against.
        self.fixture.market = market(self.fixture, asks=[], mark_price=None)
        with self.assertRaises(AgentError) as caught:
            await self.run_action(self.fixture.order(order_type="MARKET", time_in_force="IOC",
                                                     price=None))
        self.assertEqual(caught.exception.code, "invalid_order")

    async def test_cancel_kill_all_and_security_commands_use_their_exact_wire(self):
        order_id = "0x" + "aa" * 32
        await self.run_action({"type": "cancel", "order_id": order_id})
        await self.run_action({"type": "kill_all"})
        for action in ({"type": "leverage", "leverage": "100"},
                       {"type": "fee_mode", "mode": "PNL"},
                       {"type": "dead_man_switch", "deadline_ms": self.fixture.expiry(5_000)},
                       {"type": "dead_man_switch", "deadline_ms": "0"}):
            result = await self.run_action(action)
            self.assertEqual(result["recovery"]["status"], "accepted")
        cancel, kill, leverage, fee, dms, disarm = (r["action"] for r in self.fixture.admitted())
        self.assertEqual(cancel, {"type": "cancel", "order_id": order_id})
        self.assertEqual(set(kill), {"type", "client_nonce"})
        self.assertEqual((leverage["type"], leverage["leverage"], leverage["command_nonce"]),
                         ("set_leverage", "100", "1"))
        self.assertEqual((fee["type"], fee["fee_mode"], fee["command_nonce"]), ("set_fee_mode", 1, "2"))
        self.assertEqual((dms["type"], dms["command_nonce"]), ("set_dms", "3"))
        self.assertEqual((disarm["deadline_ms"], disarm["command_nonce"]), ("0", "4"))
        self.assertEqual((await self.client.snapshot())["next_nonce"], "5")

    async def test_a_signed_security_nonce_is_never_signed_again(self):
        self.fixture.refusal = lambda request: (409, not_placed(request, "rejected"))
        with self.assertRaises(AgentError) as caught:
            await self.run_action({"type": "leverage", "leverage": "5"})
        self.assertEqual(caught.exception.code, "rejected")
        self.assertFalse(self.client.has_pending_request())
        # The server still offers nonce 1, but this key already signed it once.
        snapshot = await self.client.snapshot()
        self.assertEqual((snapshot["next_command_nonce"], snapshot["next_nonce"]), ("1", "2"))
        with self.assertRaises(AgentError) as caught:
            await self.client.run({"nonce": "1", "expiresAtMs": self.fixture.expiry(),
                                   "action": {"type": "leverage", "leverage": "5"}})
        self.assertEqual(caught.exception.code, "nonce_reused")
        self.assertEqual(self.fixture.calls["admit"], 1)

    async def test_account_settings_switched_off_are_refused_before_signing(self):
        self.fixture.context["account_settings_enabled"] = False
        for action in ({"type": "leverage", "leverage": "5"}, {"type": "fee_mode", "mode": "PNL"}):
            with self.assertRaises(AgentError) as caught:
                await self.run_action(action)
            self.assertEqual(caught.exception.code, "admission_closed")
        await self.run_action({"type": "kill_all"})  # Other commands are unaffected.
        self.assertEqual(self.fixture.calls["admit"], 1)
        del self.fixture.context["account_settings_enabled"]  # Older replies omit the flag.
        await self.run_action({"type": "leverage", "leverage": "5"})

    async def test_expiry_and_nonce_are_checked_before_anything_is_signed(self):
        grant_expiry = int(self.fixture.grant["material"]["expires_at_ms"])
        for expires in ("1", str(grant_expiry + 1)):
            with self.assertRaises(AgentError) as caught:
                await self.client.run({"nonce": "1", "expiresAtMs": expires,
                                       "action": self.fixture.order()})
            self.assertEqual(caught.exception.code, "context_changed")
        with self.assertRaises(AgentError) as caught:
            await self.client.run({"nonce": "2", "expiresAtMs": self.fixture.expiry(),
                                   "action": self.fixture.order()})
        self.assertEqual(caught.exception.code, "nonce_reused")
        self.assertEqual(self.fixture.calls["admit"], 0)
        # Only the signed context reads reached the server.
        self.assertEqual(len(self.fixture.signed), self.fixture.calls["context"])

    async def test_lost_reply_is_retained_across_restart_and_never_resent(self):
        self.fixture.lost_reply = True
        with self.assertRaises(AgentError) as caught:
            await self.run_action(self.fixture.order())
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertTrue(self.client.has_pending_request())
        retained = self.client._custody.read()[2]
        self.assertEqual(set(retained), {"schema", "request"})
        self.assertEqual(retained["request"], self.fixture.admitted()[0])
        with self.assertRaises(AgentError) as caught:
            await self.run_action(self.fixture.order())
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.client.close()
        self.client = self.fixture.client(self.directory, initialize=False)
        self.assertEqual(self.client._custody.read()[2], retained)
        result = await self.client.recover_original()
        self.assertEqual((result["status"], result["action"]), ("accepted", "order"))
        self.assertFalse(self.client.has_pending_request())
        self.assertEqual(self.fixture.calls["admit"], 1)
        with self.assertRaises(AgentError) as caught:
            await self.client.recover_original()
        self.assertEqual(caught.exception.code, "no_pending_request")

    async def test_unknown_or_unbound_recovery_replies_never_clear_the_original(self):
        self.fixture.refusal = lambda request: (None, None)  # 503: nothing journaled.
        with self.assertRaises(AgentError):
            await self.run_action(self.fixture.order())
        original = copy.deepcopy(self.client._custody.read()[2])
        self.assertIsNotNone(original)
        edits = (None, lambda r: r.update(request_sha256="0x" + "11" * 32),
                 lambda r: r.update(retry_authorized=True), lambda r: r.update(proofVerified=True),
                 lambda r: r.update(status="final"), lambda r: r.update(action="cancel"),
                 lambda r: r.update(proof_backed=False), lambda r: r.update(private_key_seed=SEED),
                 # A reason only ever comes with not_placed, and not_placed always has one.
                 lambda r: r.update(reason="rejected"), lambda r: r.update(reason=None),
                 lambda r: r.update(status="not_placed"),
                 lambda r: r.update(status="not_placed", reason=None),
                 lambda r: r.update(status="not_placed", reason="timeout"))
        for edit in edits:
            self.fixture.recovery_edit = edit
            with self.subTest(edit=edit):
                if edit is None:
                    self.assertEqual((await self.client.recover_original())["status"], "unresolved")
                else:
                    with self.assertRaises(AgentError):
                        await self.client.recover_original()
                self.assertEqual(self.client._custody.read()[2], original)
        self.assertEqual(self.fixture.calls["admit"], 1)

    async def test_terminal_lookups_clear_exactly_the_original(self):
        for status, reason, outcome in (
                ("not_placed", "rejected", "rejected"),
                ("not_placed", "expired_never_admitted", "not_placed"),
                ("not_settled", None, "not_settled"), ("operator_journaled", None, "accepted")):
            self.fixture.lost_reply = True
            with self.assertRaises(AgentError):
                await self.run_action(self.fixture.order())
            self.fixture.lost_reply = False
            self.fixture.recovery_status = status
            self.fixture.recovery_reason = reason
            result = await self.client.recover_original()
            self.assertEqual((result["status"], result["wire_status"], result["reason"]),
                             (outcome, status, reason))
            self.assertFalse(self.client.has_pending_request())
            self.fixture.recovery_status, self.fixture.recovery_reason = None, "rejected"

    async def test_definitive_not_placed_refusals_clear_only_their_exact_original(self):
        for reason in ts2.NOT_PLACED:
            self.fixture.refusal = lambda request, reason=reason: (
                refusal_status(reason), not_placed(request, reason))
            with self.subTest(reason=reason), self.assertRaises(AgentError) as caught:
                await self.run_action(self.fixture.order())
            self.assertEqual(caught.exception.code, reason)
            self.assertFalse(self.client.has_pending_request())
        bad = [
            lambda r: (409, not_placed(r, "rate_limited")),
            lambda r: (429, not_placed(r, "rejected")),
            lambda r: (409, not_placed(r, "rejected", request_sha256="0x" + "11" * 32)),
            lambda r: (409, not_placed(r, "rejected", retry="later")),
            lambda r: (409, not_placed(r, "batch_closed")),
            lambda r: (409, not_placed(r, "rejected", proof_backed=True)),
            lambda r: (409, b"not json"),
        ]
        for index, refusal in enumerate(bad):
            self.fixture.refusal = refusal
            with self.subTest(case=index), self.assertRaises(AgentError) as caught:
                await self.run_action(self.fixture.order())
            self.assertEqual(caught.exception.code, "outcome_unknown")
            self.assertTrue(self.client.has_pending_request())
            self.fixture.refusal, self.fixture.recovery_status = None, "not_placed"
            self.assertEqual((await self.client.recover_original())["status"], "rejected")
            self.fixture.recovery_status = None

    async def test_an_unbound_ack_is_an_unknown_outcome(self):
        for edit in (lambda r: r.update(order_id="0x" + "11" * 32),
                     lambda r: r.update(request_sha256="0x" + "11" * 32),
                     lambda r: r.update(config_hash="0x" + "11" * 32),
                     lambda r: r.update(status="settled"), lambda r: r.update(publicFinality=True),
                     lambda r: r.pop("held")):
            self.fixture.ack_edit = edit
            with self.subTest(edit=edit), self.assertRaises(AgentError) as caught:
                await self.run_action(self.fixture.order())
            self.assertEqual(caught.exception.code, "outcome_unknown")
            self.assertTrue(self.client.has_pending_request())
            self.fixture.ack_edit = None
            self.assertEqual((await self.client.recover_original())["status"], "accepted")

    async def test_signed_account_read_parses_the_server_view_exactly(self):
        result = await self.client.account()
        context, account = result["context"], result["view"]
        self.assertEqual((context["fee_mode"], context["leverage"]), (0, "10"))
        self.assertEqual(account["account"]["wallet_balance"], "1000.5")
        self.assertEqual(account["account"]["position_quantity"], "-0.002")
        self.assertEqual(account["market"]["mark_price"], "80000.05")
        resting, held, filled = account["orders"]
        self.assertEqual((resting["side"], resting["order_type"], resting["price"]),
                         ("BUY", "LIMIT", "79990"))
        self.assertEqual((resting["quantity"], resting["filled"], resting["remaining"]),
                         ("0.003", "0.001", "0.002"))
        self.assertEqual(set(held), {"order_id", "status", "lineage", "admitted_at_ms"})
        self.assertEqual(filled["time_in_force"], "IOC")
        soft, settled = account["fills"]
        self.assertEqual((soft["price"], soft["quantity"], soft["fee"]), ("80000.2", "0.002", "0.0192"))
        self.assertEqual(soft["settlement"], {"state": "operator_soft"})
        self.assertEqual(settled["settlement"]["finalized_at_block"], "110")
        self.assertEqual(account["readiness"]["reason"], "open")
        self.assertEqual(self.fixture.calls["context"], 1)
        self.assertEqual(self.fixture.calls["read"], 1)
        self.assertEqual(self.fixture.calls["admit"], 0)
        # Without the context: one signed read only.
        alone = await self.client.account(with_context=False)
        self.assertIsNone(alone["context"])
        self.assertEqual(self.fixture.calls["context"], 1)
        # An empty account (no credit yet) is a valid reply.
        self.fixture.view_edit = lambda r: r.update(
            account={"soft": None, "settled": None, "settled_final": None}, orders=[], fills=[])
        self.assertIsNone((await self.client.account())["view"]["account"])

    async def test_account_read_refuses_unbound_or_malformed_views(self):
        edits = (
            lambda r: r.update(settlement="0x" + "09" * 20),
            lambda r: r.update(proofVerified=True),
            lambda r: r.update(tradingEnabled=True, admission="funding_owed"),
            lambda r: r.update(private_key_seed=SEED),
            lambda r: r["market"]["book"].update(depth=19),
            lambda r: r["account"]["soft"].update(wallet_balance=1000.5),
            lambda r: r["account"]["soft"].update(reserved_order_margin="-1"),
            lambda r: r["orders"][1].update(detail={}),
            lambda r: r["orders"][0].update(status="open"),
            lambda r: r["orders"][0]["detail"].update(filled_lots="4"),
            lambda r: r["fills"][0].update(settlement={"state": "proven"}),
            lambda r: r["fills"][1]["settlement"].update(finalized_at_block=None),
            lambda r: r["readiness"].update(enabled=False),
            lambda r: r["readiness"].update(source="operator_independent_l1"),
            lambda r: r["history"].update(row_limit=65),
        )
        for edit in edits:
            self.fixture.view_edit = edit
            with self.subTest(edit=edit), self.assertRaises(AgentError) as caught:
                await self.client.account(with_context=False)
            self.assertEqual(caught.exception.code, "response_invalid")
        self.assertFalse(self.client.has_pending_request())

    async def test_submit_reads_the_context_once_and_picks_nonce_and_expiry(self):
        start = int(time.time() * 1000)
        result = await self.client.submit(self.fixture.order(), lifetime_ms=3_600_000)
        self.assertEqual(result["recovery"]["status"], "accepted")
        self.assertEqual((self.fixture.calls["context"], self.fixture.calls["admit"]), (1, 1))
        expires = int(self.fixture.admitted()[0]["action"]["expires_at_ms"])
        self.assertTrue(start + 3_600_000 <= expires <= int(time.time() * 1000) + 3_600_000)
        await self.client.submit({"type": "leverage", "leverage": "3"}, lifetime_ms=30_000)
        self.assertEqual(self.fixture.admitted()[-1]["action"]["command_nonce"], "1")
        for lifetime in (1_000, 23 * 60 * 60 * 1000 + 1, "60000"):
            with self.subTest(lifetime=lifetime), self.assertRaises(AgentError):
                await self.client.submit(self.fixture.order(), lifetime_ms=lifetime)
        self.assertEqual(self.fixture.calls["admit"], 2)

    async def test_closed_admission_refuses_locally_before_signing(self):
        self.fixture.context["admission"] = {"open": False, "reasons": ["funding_owed"]}
        for action in (self.fixture.order(), self.fixture.order(reduce_only=True),
                       {"type": "leverage", "leverage": "2"},
                       {"type": "dead_man_switch", "deadline_ms": "0"}):
            with self.subTest(action=action), self.assertRaises(AgentError) as caught:
                await self.client.submit(action, lifetime_ms=30_000)
            self.assertEqual(caught.exception.code, "admission_closed")
        self.assertEqual(self.fixture.calls["admit"], 0)
        self.assertFalse(self.client.has_pending_request())
        self.assertEqual(self.client.last_context()["admission"]["reasons"], ["funding_owed"])
        # Cancels and kill-all reduce risk: the server decides, during funding_owed too.
        await self.client.submit({"type": "cancel", "order_id": "0x" + "aa" * 32},
                                 lifetime_ms=30_000)
        await self.client.submit({"type": "kill_all"}, lifetime_ms=30_000)
        self.assertEqual(self.fixture.calls["admit"], 2)
        # A lagging mark admits reduce-only orders, never new risk.
        self.fixture.context["admission"] = {"open": False, "reasons": ["mark_lagging"]}
        await self.client.submit(self.fixture.order(reduce_only=True), lifetime_ms=30_000)
        with self.assertRaises(AgentError) as caught:
            await self.client.submit(self.fixture.order(), lifetime_ms=30_000)
        self.assertEqual(caught.exception.code, "admission_closed")
        self.assertEqual(self.fixture.calls["admit"], 3)

    async def test_market_orders_take_a_slippage_bound_and_fall_back_to_the_mark(self):
        def market_order(**changes):
            return self.fixture.order(**{"order_type": "MARKET", "time_in_force": "IOC",
                                         "price": None, **changes})

        await self.client.submit(market_order(max_slippage_bps=100), lifetime_ms=30_000)
        # Best ask 80000.2 * 1.01 = 80800.202 -> up to 80800.3.
        self.assertEqual(self.fixture.admitted()[-1]["action"]["limit_price_ticks"], "808003")
        self.fixture.market = market(self.fixture, asks=[])
        await self.client.submit(market_order(), lifetime_ms=30_000)
        # No ask: the mark 80000.05 * 1.005 = 80400.05025 -> up to 80400.1.
        self.assertEqual(self.fixture.admitted()[-1]["action"]["limit_price_ticks"], "804001")
        for changes in ({"max_slippage_bps": 0}, {"max_slippage_bps": 1001},
                        {"max_slippage_bps": "50"}, {"max_slippage_bps": 50, "price": "80000"}):
            with self.subTest(changes=changes), self.assertRaises(AgentError) as caught:
                await self.client.submit(market_order(**changes), lifetime_ms=30_000)
            self.assertEqual(caught.exception.code, "invalid_order")
        with self.assertRaises(AgentError) as caught:
            await self.client.submit(self.fixture.order(max_slippage_bps=50), lifetime_ms=30_000)
        self.assertEqual(caught.exception.code, "invalid_order")
        self.fixture.market = market(self.fixture, asks=[], mark_price=None)
        with self.assertRaises(AgentError) as caught:
            await self.client.submit(market_order(), lifetime_ms=30_000)
        self.assertEqual(caught.exception.code, "invalid_order")
        self.assertEqual(self.fixture.calls["admit"], 2)

    async def test_custody_is_never_reinitialized_or_adopted_by_another_key(self):
        await self.run_action({"type": "kill_all"})
        self.client.close()
        with self.assertRaises(AgentError) as caught:
            self.fixture.client(self.directory, initialize=True)
        self.assertEqual(caught.exception.code, "unavailable")
        other = NativeFixture(seed="0x" + "4d" * 32, nonce="2")
        with self.assertRaises(AgentError):
            other.client(self.directory, initialize=False)
        missing = tempfile.mkdtemp(prefix="ts2-native-missing-")
        try:
            os.chmod(missing, 0o700)
            with self.assertRaises(AgentError):
                self.fixture.client(missing, initialize=False)
            self.assertFalse(Path(missing, "original.sqlite").exists())
        finally:
            os.rmdir(missing)
        self.client = self.fixture.client(self.directory, initialize=False)
        with self.assertRaises(AgentError) as caught:
            self.fixture.client(self.directory, initialize=False)  # Same signer object.
        self.assertEqual(caught.exception.code, "busy")

    async def test_from_credentials_refuses_v4_keys_and_signs_with_the_downloaded_key(self):
        fixture = self.fixture
        key_dir = os.path.realpath(tempfile.mkdtemp(prefix="ts2-native-key-"))
        try:
            os.chmod(key_dir, 0o700)
            path = Path(key_dir, "fairvenue-TESTNET-agent.json")
            path.write_text(json.dumps(fixture.export()))
            path.chmod(0o600)
            continuity = Path(key_dir, "continuity")
            continuity.mkdir(mode=0o700)
            self.client.close()
            with patch.object(native, "HttpTransport", return_value=fixture):
                client = NativeTestnetClient.from_credentials(
                    str(path), policy=fixture.policy, approved_api_url=API,
                    custody_path=str(continuity / "original.sqlite"), initialize=True)
            try:
                snapshot = await client.snapshot()
                result = await client.run({"nonce": snapshot["next_nonce"],
                                           "expiresAtMs": fixture.expiry(),
                                           "action": fixture.order()})
                self.assertEqual(result["recovery"]["status"], "accepted")
            finally:
                client.close()
            v4 = Path(key_dir, "fairvenue-v4.json")
            v4.write_text(json.dumps({"schema": ts2.V4_KEY_SCHEMA, "private_key_seed": SEED}))
            v4.chmod(0o600)
            with self.assertRaises(AgentError) as caught:
                NativeTestnetClient.from_credentials(
                    str(v4), policy=fixture.policy, approved_api_url=API,
                    custody_path=str(continuity / "other.sqlite"), initialize=True)
            self.assertEqual(str(caught.exception), "TS2: create a new bot key.")
        finally:
            for root, directories, files in os.walk(key_dir, topdown=False):
                for name in files:
                    os.unlink(os.path.join(root, name))
                for name in directories:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(key_dir)
        self.client = fixture.client(self.directory, initialize=False)


class TransportPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_agent_paths_and_the_public_market_read(self):
        transport = HttpTransport("https://native-testnet.example")
        for path in ("/api/v1/exchange", "/v1/onboarding/account", "/api/v1/info?x=1"):
            with self.subTest(path=path), self.assertRaises(AgentError):
                await transport.post(path, b"{}", 5)
        from fairvenue_schema6 import transport as module

        self.assertEqual(module._limit("/api/v1/info"), 131072)
        self.assertEqual(module._limit("/v1/ts2/agent/read"), 131072)
        self.assertEqual(module._limit("/v1/agent/admit"), 16384)
        self.assertIn("/api/v1/info", module._PATHS)
        self.assertIn("/v1/ts2/agent/read", module._PATHS)
        self.assertNotIn("/v1/ts2/agent/exchange", module._PATHS)


if __name__ == "__main__":
    unittest.main()
