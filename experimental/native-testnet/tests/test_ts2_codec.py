"""TS2 codec parity with the shared JS/Rust vectors. TEST keys only, no network."""

import copy
import json
from pathlib import Path
import unittest

from fairvenue_schema6 import AgentError, CryptoPrimitives, ts2

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
CLIENT = json.loads((FIXTURES / "ts2-client-vectors-v1.json").read_text())
SECURITY = json.loads((FIXTURES / "ts2-client-security-vectors-v1.json").read_text())
# The canonical vectors, when this package sits in its source tree.
UPSTREAM = ROOT.parents[1] / "trade-settlement/fixtures"
PINS = {"chain_id": CLIENT["domain"]["chainId"], "settlement": CLIENT["domain"]["settlement"],
        "config_hash": CLIENT["domain"]["configHash"]}
# `requestDigest` of the reviewed JS codec (trade-settlement/client/codec.mjs) for
# complete requests built from the client vectors. The JS codec reproduces each
# vector signature exactly. The security vectors carry their own Rust/JS digests.
JS_REQUEST_SHA256 = {
    "limit GTC buy 0.010 @ 60000.0":
        "0x2f14d44fe7fdf77fb808f3d712a5ba96ca6a2ddafdebdf5152fc5ba9bf6e8eef",
    "limit GTC sell post-only 1.234 @ 60001.5":
        "0x0b23c967daa1472c41b0713e9b4481122545ea21e920f2d4a6b2a059141723f0",
    "market IOC sell, worst price 59000.0":
        "0xb2f8d3e968291d73a75bb128d7f830b890ab2e785da12a52289389aafa04245f",
    "limit FOK buy reduce-only":
        "0x99e2758e8bae238229f4a3fcba5340dc21ae5454799b668ba38d7bf253a41523",
    "cancel": "0x1d21817719b5a9ac740988759daf4c8819bb84d65b7ebcc50ac6a7b09bfa7eb2",
}


def word(account):
    return "0x" + int(account).to_bytes(32, "big").hex()


def order_action(intent):
    return {"type": "order", "delegation_nonce": intent["delegationNonce"],
            "client_nonce": intent["clientNonce"], "side": intent["side"], "kind": intent["kind"],
            "time_in_force": intent["timeInForce"], "post_only": intent["postOnly"],
            "reduce_only": intent["reduceOnly"], "fee_mode": intent["feeMode"],
            "limit_price_ticks": intent["limitPriceTicks"],
            "quantity_lots": intent["quantityLots"], "expires_at_ms": intent["expiresAtMs"]}


def sign(secret, message):
    from nacl.signing import SigningKey

    return "0x" + SigningKey(bytes.fromhex(secret[2:])).sign(message).signature.hex()


def request(account_key, agent, signature, action, pins=PINS):
    return {"schema": ts2.REQUEST_SCHEMA, **pins, "account_key": account_key,
            "agent_public_key": agent, "signature": signature, "action": action}


class VectorParityTests(unittest.TestCase):
    def setUp(self):
        self.crypto = CryptoPrimitives()

    def test_package_vectors_are_the_shared_upstream_vectors(self):
        if not UPSTREAM.is_dir():
            self.skipTest("published package: SOURCE.json pins these fixture hashes")
        for name in ("ts2-client-vectors-v1.json", "ts2-client-security-vectors-v1.json"):
            self.assertEqual((FIXTURES / name).read_bytes(), (UPSTREAM / name).read_bytes())

    def test_order_ids_signing_messages_signatures_and_request_digests(self):
        self.assertEqual(len(CLIENT["intents"]), 4)
        for case in CLIENT["intents"]:
            with self.subTest(case=case["name"]):
                intent = case["intent"]
                action = order_action(intent)
                message, order_id = ts2.signing_message(
                    PINS, word(intent["account"]), intent["agent"], action, self.crypto)
                self.assertEqual(order_id, case["orderId"])
                self.assertEqual("0x" + message.hex(), case["signingMessage"])
                core = bytes.fromhex(case["intentCorePreimage"][2:])
                self.assertEqual(self.crypto.digest(core), case["intentCoreHash"])
                self.assertEqual(self.crypto.digest(bytes.fromhex(case["orderIdPreimage"][2:])),
                                 case["orderId"])
                signature = sign(case["agentSecret"], message)
                self.assertEqual(signature, case["signature"])
                signed = request(word(intent["account"]), intent["agent"], signature, action)
                self.assertEqual(ts2.request_copy(signed, PINS, self.crypto), signed)
                self.assertEqual(ts2.request_sha256(signed), JS_REQUEST_SHA256[case["name"]])

    def test_cancel_signing_message_signature_and_request_digest(self):
        vector = CLIENT["cancel"]
        cancel = vector["request"]
        action = {"type": "cancel", "order_id": cancel["orderId"]}
        message, order_id = ts2.signing_message(
            PINS, word(cancel["account"]), cancel["agent"], action, self.crypto)
        self.assertIsNone(order_id)
        self.assertEqual("0x" + message.hex(), vector["signingMessage"])
        signature = sign(vector["agentSecret"], message)
        self.assertEqual(signature, cancel["signature"])
        signed = request(word(cancel["account"]), cancel["agent"], signature, action)
        self.assertEqual(ts2.request_sha256(signed), JS_REQUEST_SHA256["cancel"])

    def test_kill_all_and_security_vectors_exact(self):
        kinds = []
        secret = CLIENT["intents"][0]["agentSecret"]
        for vector in SECURITY["vectors"]:
            signed = vector["request"]
            kinds.append(signed["action"]["type"])
            with self.subTest(kind=kinds[-1]):
                pins = {key: signed[key] for key in ("chain_id", "settlement", "config_hash")}
                message, order_id = ts2.signing_message(
                    pins, signed["account_key"], signed["agent_public_key"], signed["action"],
                    self.crypto)
                self.assertIsNone(order_id)
                self.assertEqual("0x" + message.hex(), vector["signing_message"])
                self.assertEqual(sign(secret, message), signed["signature"])
                self.assertEqual(ts2.request_copy(signed, pins, self.crypto), signed)
                self.assertEqual(ts2.request_sha256(signed), vector["request_sha256"])
                # Every JSON number stays a number and every u64 a decimal string.
                self.assertIn('"chain_id":31337', ts2.canonical(signed))
        self.assertEqual(kinds, ["kill_all", "set_dms", "set_fee_mode", "set_leverage"])

    def test_tampered_or_foreign_requests_refused(self):
        signed = copy.deepcopy(SECURITY["vectors"][3]["request"])
        pins = {key: signed[key] for key in ("chain_id", "settlement", "config_hash")}
        changes = [
            lambda r: r["action"].update(leverage="99"),
            lambda r: r["action"].update(command_nonce="1"),
            lambda r: r.update(signature="0x" + "00" * 64),
            lambda r: r.update(account_key=word(2)),
            lambda r: r.update(schema="fairvenue-v1-agent-request-v1"),
            lambda r: r.update(extra=True),
        ]
        for index, change in enumerate(changes):
            tampered = copy.deepcopy(signed)
            change(tampered)
            with self.subTest(case=index), self.assertRaises(AgentError):
                ts2.request_copy(tampered, pins, self.crypto)
        with self.assertRaises(AgentError):
            ts2.request_copy(signed, ts2.PINS, self.crypto)  # Other deployment pins.


class ActionShapeTests(unittest.TestCase):
    def test_actions_mirror_the_reviewed_codec(self):
        good = order_action(CLIENT["intents"][0]["intent"])
        self.assertEqual(ts2.action_copy(good), good)
        bad = [
            {**good, "kind": 1, "time_in_force": 0},
            {**good, "kind": 1, "time_in_force": 1, "post_only": True},
            {**good, "post_only": True, "time_in_force": 1},
            {**good, "side": True},
            {**good, "side": 2},
            {**good, "time_in_force": 3},
            {**good, "fee_mode": "0"},
            {**good, "quantity_lots": "0"},
            {**good, "limit_price_ticks": "07"},
            {**good, "client_nonce": "18446744073709551616"},
            {**good, "delegation_nonce": "0"},
            {**good, "extra": 1},
            {"type": "cancel", "order_id": "0x" + "00" * 32},
            {"type": "cancel", "order_id": "42"},
            {"type": "kill_all"},
            {"type": "set_leverage", "delegation_nonce": "1", "command_nonce": "1",
             "expires_at_ms": "1", "leverage": "101"},
            {"type": "set_leverage", "delegation_nonce": "1", "command_nonce": "1",
             "expires_at_ms": "1", "leverage": 10},
            {"type": "set_fee_mode", "delegation_nonce": "1", "command_nonce": "0",
             "expires_at_ms": "1", "fee_mode": 1},
            {"type": "modify", "order_id": "0x" + "11" * 32},
            {"type": ["order"]},
        ]
        for index, action in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(AgentError):
                ts2.action_copy(action)

    def test_canonical_json_refuses_floats_and_unsafe_integers(self):
        self.assertEqual(ts2.canonical({"b": [1, "x"], "a": None}), '{"a":null,"b":[1,"x"]}')
        for value in ({"a": 1.5}, {"a": -1}, {"a": 1 << 53}, {"a": "é"}, {"a": "\n"}):
            with self.subTest(value=value), self.assertRaises(AgentError):
                ts2.canonical(value)

    def test_ticks_and_lots_are_exact_never_rounded(self):
        self.assertEqual(ts2.units("60000.0", "0.1"), "600000")
        self.assertEqual(ts2.units("60001.50", "0.1"), "600015")
        self.assertEqual(ts2.units("1.234", "0.001"), "1234")
        for value, unit in (("60000.05", "0.1"), ("0.0015", "0.001"), ("0", "0.1"),
                            ("1e3", "1"), ("-1", "1"), (1, "1"), ("1", "0")):
            with self.subTest(value=value, unit=unit), self.assertRaises(AgentError) as caught:
                ts2.units(value, unit)
            self.assertEqual(caught.exception.code, "invalid_order")

    def test_market_protection_rounds_away_from_the_book(self):
        # 0.5% beyond the best opposite price: up for BUY, down for SELL.
        self.assertEqual(ts2.protection_ticks("60000", "0.1", "BUY"), "603000")
        self.assertEqual(ts2.protection_ticks("60000", "0.1", "SELL"), "597000")
        self.assertEqual(ts2.protection_ticks("60000.3", "0.1", "BUY"), "603004")
        self.assertEqual(ts2.protection_ticks("60000.3", "0.1", "SELL"), "597002")
        self.assertEqual(ts2.units("59000.07", "0.1", "down"), "590000")
        self.assertEqual(ts2.units("59000.01", "0.1", "up"), "590001")
        # A chosen width: 2% here. The default stays the browser's 0.5%.
        self.assertEqual(ts2.protection_ticks("60000", "0.1", "BUY", 200), "612000")
        self.assertEqual(ts2.protection_ticks("60000.3", "0.1", "SELL", 200), "588002")
        for bps in (0, 1001, "50", 50.0, True):
            with self.subTest(bps=bps), self.assertRaises(AgentError) as caught:
                ts2.protection_ticks("60000", "0.1", "BUY", bps)
            self.assertEqual(caught.exception.code, "invalid_order")

    def test_exact_decimal_conversions_of_ticks_lots_and_micros(self):
        self.assertEqual(ts2.times("799900", "0.1"), "79990")
        self.assertEqual(ts2.times("3", "0.001"), "0.003")
        self.assertEqual(ts2.times("0", "0.001"), "0")
        self.assertEqual(ts2.micros("80000050000"), "80000.05")
        self.assertEqual(ts2.micros("1"), "0.000001")

    def test_account_read_signs_the_native_kind_1_message(self):
        account = "0x" + "00" * 31 + "07"
        message = ts2.read_signing_message(PINS, account, "1700000000000")
        self.assertEqual(message, b"FV_TS2_READ_SIG\0" + bytes.fromhex(PINS["config_hash"][2:])
                         + b"\x01" + (7).to_bytes(8, "big") + bytes(32)
                         + (1700000000000).to_bytes(8, "big"))
        for expires in ("0", "", 1700000000000):
            with self.subTest(expires=expires), self.assertRaises(AgentError):
                ts2.read_signing_message(PINS, account, expires)

    def test_grant_material_shape_rights_and_lifetime(self):
        material = {"schema": ts2.MATERIAL_SCHEMA, **ts2.PINS, "owner": "0x" + "70" * 20,
                    "account_key": word(7), "agent_public_key": "0x" + "6b" * 32,
                    "permissions": 11, "nonce": "1", "issued_at_ms": "1800000000000",
                    "expires_at_ms": "1800604800000", "max_expiry_ms": "1800604800000",
                    "can_withdraw": False}
        grant = {"material": material, "signature": "0x" + "43" * 64 + "1b"}
        self.assertEqual(ts2.grant_copy(grant, ts2.PINS), grant)
        bad = [
            {"permissions": 4}, {"permissions": 16}, {"can_withdraw": True},
            {"max_expiry_ms": "1800604800001"}, {"expires_at_ms": "1802592000001",
                                                 "max_expiry_ms": "1802592000001"},
            {"account_key": "0x" + "01" * 32}, {"account_key": word(0)},
            {"config_hash": "0x" + "11" * 32}, {"schema": "fairvenue-v1-agent-public-grant-v1"},
        ]
        for index, change in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(AgentError):
                ts2.grant_copy({**grant, "material": {**material, **change}}, ts2.PINS)
        with self.assertRaises(AgentError):
            ts2.grant_copy({**grant, "signature": "0x" + "43" * 64}, ts2.PINS)


if __name__ == "__main__":
    unittest.main()
