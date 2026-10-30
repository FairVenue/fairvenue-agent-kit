"""Private synthetic exports only: no HTTP, live credentials or activation evidence."""

import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fairvenue_schema6 import (
    AgentClient, AgentError, CryptoPrimitives, build_agent_command_signables,
    load_native_testnet_agent_key, load_v1_agent_key,
)
from fairvenue_schema6 import key_file

SETTLEMENT = "0xaaebec860fc2ec7f77379ac3c4b892266a76bcbf"
MOCK_TOKEN = "0x2b62f3c0663d9590113e24ec588018bcb81a7d29"
API_URL = "https://native-testnet.example/"


def fixture():
    from nacl.signing import SigningKey

    key = SigningKey.generate()  # Ephemeral local test key, never an installed principal.
    policy = {
        "domain": {"chain_id": 421614, "settlement": list(bytes.fromhex(SETTLEMENT[2:]))},
        "market_id": [8] * 32,
        "config_version": "3", "epoch_ms": "100", "absolute_error_ms": "20",
        "max_disagreement_epochs": "1", "max_lifetime_epochs": "100",
        "max_oracle_age_ms": "1000", "max_oracle_future_ms": "0",
        "gateways": [{"gateway_id": index, "key_id": index,
                      "public_key": "0x" + bytes(SigningKey(bytes([index]) * 32).verify_key).hex(),
                      "first_epoch": "0", "end_epoch": str((1 << 64) - 1)}
                     for index in (1, 2, 3)],
    }
    grant = {
        "schema": "fairvenue-v1-agent-public-grant-v1", "chainId": "421614",
        "settlement": SETTLEMENT, "owner": "0x" + "04" * 20,
        "account": "0x" + "0" * 63 + "7",
        "agentPublicKey": "0x" + bytes(key.verify_key).hex(),
        "permissions": 15, "delegationNonce": "1", "issuedAtMs": "0",
        "expiresAtMs": "40000", "maxExpiryMs": "40000", "canWithdraw": False,
    }
    return policy, {
        "schema": "fairvenue-native-testnet-agent-v1", "environment": "TESTNET",
        "settlement_mode": "mock_token_sp1_operator_beta", "chain_id": 421614,
        "settlement": SETTLEMENT, "mock_token": MOCK_TOKEN, "api_namespace": "/v1",
        "api_url": API_URL, "agent_policy": copy.deepcopy(policy), "public_grant": grant,
        "private_key_seed": "0x" + bytes(key).hex(),
    }


def write(directory, value, *, name="key.json", mode=0o600):
    path = str((Path(directory).resolve() / name))
    raw = value if type(value) is bytes else json.dumps(value, separators=(",", ":")).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as file:
        file.write(raw)
    return path


class NativeCredentialTests(unittest.TestCase):
    def load(self, path, policy, api=API_URL):
        return load_native_testnet_agent_key(path, policy=policy, approved_api_url=api)

    def test_browser_shape_owned_file_signs_exact_existing_native_codec_without_io(self):
        for mode in (0o400, 0o600):
            policy, exported = fixture()
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported, mode=mode)
                before = Path(path).read_bytes()
                with patch("fairvenue_schema6.transport._http", side_effect=AssertionError("no I/O")):
                    signer, grant = self.load(path, policy)
                context = {
                    "authenticated": True, "session_scope": "01" * 32, "owner": grant["owner"],
                    "protocol_time_ms": "1000", "prefix": {
                        "domain": policy["domain"],
                        "opening": {"previous_root": [1] * 32, "batch": 1, "generation": 1,
                                    "opening_commitment": [2] * 32},
                        "head": {"count": 0, "chain_hash": "0x" + "00" * 32,
                                 "public_data_hash": "0x" + "00" * 32, "next_root": "0x" + "01" * 32},
                    },
                }
                signables = build_agent_command_signables(
                    policy=policy, grant=grant, context=context,
                    input={"nonce": "1", "expiresAtMs": "20000",
                           "action": {"type": "cancel", "order_id": "1"}},
                )
                message = bytes.fromhex(signables["intent_signing_bytes"][2:])
                signature = asyncio.run(signer.sign_bytes(message))
                self.assertTrue(CryptoPrimitives().verify("0x" + signature.hex(), message, signer.public_key))
                self.assertEqual(grant, exported["public_grant"])
                self.assertEqual(signer.public_key, grant["agentPublicKey"])
                self.assertNotIn(exported["private_key_seed"][2:], repr(signer))
                self.assertEqual(Path(path).read_bytes(), before)
                self.assertEqual(os.stat(path).st_mode & 0o777, mode)

    def test_exact_testnet_deployment_labels_policy_grant_and_seed_required(self):
        changes = [
            lambda v: v.update(schema="arena-agent-key-v1"),
            lambda v: v.pop("schema"),
            lambda v: v.update(environment="PRODUCTION"),
            lambda v: v.update(settlement_mode="virtual"),
            lambda v: v.update(chain_id=1),
            lambda v: v.update(chain_id=True),
            lambda v: v.update(chain_id="421614"),
            lambda v: v.update(settlement="0x" + "09" * 20),
            lambda v: v.update(mock_token="0xaf88d065e77c8cc2239327c5edb3a432268e5831"),
            lambda v: v.update(api_namespace="/api/v1"),
            lambda v: v.update(private_key_seed="0x" + "00" * 32),
            lambda v: v.update(private_key_seed="0x" + "00" * 31),
            lambda v: v.update(private_key="must not accept a second secret field"),
            lambda v: v["public_grant"].update(canWithdraw=True),
            lambda v: v["public_grant"].update(permissions=16),
            lambda v: v["public_grant"].update(agentPublicKey="0x01" + "00" * 31),
            lambda v: v["public_grant"].update(settlement="0x" + "09" * 20),
            lambda v: v["agent_policy"].update(epoch_ms="99"),
            lambda v: v["agent_policy"].update(config_version="4"),
            lambda v: v["agent_policy"]["domain"].update(chain_id=1),
            lambda v: v["agent_policy"]["gateways"][1].update(
                public_key=v["agent_policy"]["gateways"][0]["public_key"]),
        ]
        for index, change in enumerate(changes):
            policy, exported = fixture()
            change(exported)
            with self.subTest(case=index), tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported)
                before = Path(path).read_bytes()
                with self.assertRaises(AgentError) as caught:
                    self.load(path, policy)
                self.assertNotIn(path, str(caught.exception))
                self.assertNotIn(exported["private_key_seed"], str(caught.exception))
                self.assertEqual(Path(path).read_bytes(), before)

    def test_previous_season_credentials_refuse_even_with_matching_old_policy(self):
        policy, exported = fixture()
        old_settlement = "0x8e50a346a3121ae03ccae6c64330981b2c18e9e9"
        policy["domain"]["settlement"] = list(bytes.fromhex(old_settlement[2:]))
        exported["settlement"] = old_settlement
        exported["mock_token"] = "0xed3fd1b885102d77ed13f8d576e33965ac8d4215"
        exported["agent_policy"] = copy.deepcopy(policy)
        exported["public_grant"]["settlement"] = old_settlement
        with tempfile.TemporaryDirectory(prefix="native-old-season-offline-") as directory:
            path = write(directory, exported)
            before = Path(path).read_bytes()
            with patch("fairvenue_schema6.transport._http", side_effect=AssertionError("no I/O")):
                with self.assertRaises(AgentError):
                    self.load(path, policy)
            self.assertEqual(Path(path).read_bytes(), before)

    def test_approved_origin_not_discovered_from_file_or_redirectable(self):
        invalid = ("https://other.example/", "http://127.0.0.1/", "https://native-testnet.example",
                   "https://native-testnet.example/path/", "https://user:pass@native-testnet.example/",
                   "https://native-testnet.example/?q=1", "https://native-testnet.example/#x",
                   "https://NATIVE-TESTNET.example/", "https://native-testnet.example:443/",
                   "\nhttps://native-testnet.example/", "https://native-testnet.example/\n")
        for api in invalid:
            policy, exported = fixture()
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported)
                with self.assertRaises(AgentError):
                    self.load(path, policy, api)
            # Even a matching explicit approval cannot make a malformed URL valid.
            if api != "https://other.example/":
                exported["api_url"] = api
                with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                    with self.assertRaises(AgentError):
                        self.load(write(directory, exported), policy, api)
        policy, exported = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            with self.assertRaises(AgentError):
                self.load(write(directory, exported), policy, None)

    def test_installed_policy_cannot_be_replaced_by_embedded_policy(self):
        policy, exported = fixture()
        for change in (
            lambda p: p.update(max_lifetime_epochs="101"),
            lambda p: p["domain"].update(settlement=[9] * 20),
            lambda p: p["gateways"][0].update(end_epoch="18446744073709551614"),
        ):
            installed = copy.deepcopy(policy)
            change(installed)
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                with self.assertRaises(AgentError):
                    self.load(write(directory, exported), installed)
        # The same immutable roster may use a different input ordering.
        installed = copy.deepcopy(policy)
        installed["gateways"].reverse()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            self.load(write(directory, exported), installed)

    def test_legacy_isolated_schema_remains_separate_not_silently_converted(self):
        policy, exported = fixture()
        legacy = {"schema": "fairvenue-isolated-v1-agent-key-v1", "chain_id": 421614,
                  "settlement": SETTLEMENT, "agent_public_key": exported["public_grant"]["agentPublicKey"],
                  "public_grant": exported["public_grant"], "private_key": exported["private_key_seed"]}
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            old_path = write(directory, legacy, name="old.json")
            new_path = write(directory, exported)
            old_signer, grant = load_v1_agent_key(old_path, policy=policy)
            new_signer, new_grant = self.load(new_path, policy)
            self.assertEqual((old_signer.public_key, grant), (new_signer.public_key, new_grant))
            with self.assertRaises(AgentError):
                self.load(old_path, policy)
            with self.assertRaises(AgentError):
                load_v1_agent_key(new_path, policy=policy)

    def test_private_regular_file_only_without_permission_repair(self):
        policy, exported = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            path = write(directory, exported)
            link = str(Path(directory).resolve() / "link")
            os.symlink(path, link)
            with self.assertRaises(AgentError):
                self.load(link, policy)
            os.unlink(link)
            os.link(path, link)
            with self.assertRaises(AgentError):
                self.load(path, policy)
            os.unlink(link)
            os.chmod(path, 0o644)
            with self.assertRaises(AgentError):
                self.load(path, policy)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            fifo = str(Path(directory).resolve() / "fifo")
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(AgentError):
                self.load(fifo, policy)

    def test_duplicate_unknown_oversize_and_descriptor_mutation_refused(self):
        policy, exported = fixture()
        raw = json.dumps(exported, separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            for index, data in enumerate((raw[:-1] + b',"chain_id":421614}', b"{bad", b" " * 16385)):
                with self.assertRaises(AgentError):
                    self.load(write(directory, data, name=f"bad-{index}"), policy)
            path = write(directory, exported)
            original = key_file.grant_copy

            def change_after_read(*args):
                os.chmod(path, 0o400)
                return original(*args)

            with patch.object(key_file, "grant_copy", side_effect=change_after_read), self.assertRaises(AgentError):
                self.load(path, policy)

    def test_loading_does_not_open_existing_trading_activation_gate(self):
        policy, exported = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            signer, grant = self.load(write(directory, exported), policy)

            class NoTransport:
                async def post(self, *_):
                    raise AssertionError("no HTTP")

            async def no_context():
                raise AssertionError("no context")

            client = AgentClient(policy=policy, grant=grant, signer=signer,
                                 current_context=no_context, transport=NoTransport())
            with self.assertRaises(AgentError) as caught:
                asyncio.run(client.run(None))
            self.assertEqual(caught.exception.code, "activation_closed")


if __name__ == "__main__":
    unittest.main()
