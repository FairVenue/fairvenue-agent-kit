"""Private synthetic TS2 bot key exports only: no HTTP, live credentials or activation."""

import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fairvenue_schema6 import (
    AgentError, CryptoPrimitives, load_native_testnet_agent_key, load_v1_agent_key, ts2,
)

API_URL = "https://native-testnet.example/"
# Synthetic 65-byte owner signature. The server, never this package, recovers it.
OWNER_SIGNATURE = "0x" + "5a" * 64 + "1b"


def fixture(*, issued=0, expires=2_592_000_000):
    """``(installed pins, exported TS2 bot key)`` with an ephemeral local test key."""
    from nacl.signing import SigningKey

    key = SigningKey.generate()  # Ephemeral local test key, never an installed principal.
    material = {
        "schema": ts2.MATERIAL_SCHEMA, **ts2.PINS, "owner": "0x" + "04" * 20,
        "account_key": "0x" + "00" * 31 + "07", "agent_public_key": "0x" + bytes(key.verify_key).hex(),
        "permissions": 11, "nonce": "1", "issued_at_ms": str(issued),
        "expires_at_ms": str(expires), "max_expiry_ms": str(expires), "can_withdraw": False,
    }
    return copy.deepcopy(ts2.PINS), {
        "schema": ts2.KEY_SCHEMA, "environment": "TESTNET", **ts2.PINS, "api_url": API_URL,
        "delegation": {"material": material, "signature": OWNER_SIGNATURE},
        "private_key_seed": "0x" + bytes(key).hex(),
    }


def write(directory, value, *, name="key.json", mode=0o600):
    path = str((Path(directory).resolve() / name))
    raw = value if type(value) is bytes else json.dumps(value, separators=(",", ":")).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as file:
        file.write(raw)
    return path


def v4_export():
    """The retired V4 bot key shape (public fields only matter: it is refused by name)."""
    return {"schema": ts2.V4_KEY_SCHEMA, "environment": "TESTNET",
            "settlement_mode": "mock_token_sp1_operator_beta", "chain_id": 421614,
            "settlement": "0xaaebec860fc2ec7f77379ac3c4b892266a76bcbf",
            "mock_token": "0x2b62f3c0663d9590113e24ec588018bcb81a7d29", "api_namespace": "/v1",
            "api_url": API_URL, "agent_policy": {}, "public_grant": {},
            "private_key_seed": "0x" + "42" * 32}


class NativeCredentialTests(unittest.TestCase):
    def load(self, path, policy, api=API_URL):
        return load_native_testnet_agent_key(path, policy=policy, approved_api_url=api)

    def test_browser_shape_owned_file_signs_exact_ts2_codec_without_io(self):
        for mode in (0o400, 0o600):
            pins, exported = fixture()
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported, mode=mode)
                before = Path(path).read_bytes()
                with patch("fairvenue_schema6.transport._http", side_effect=AssertionError("no I/O")):
                    signer, grant = self.load(path, pins)
                material = grant["material"]
                action = {"type": "kill_all", "client_nonce": "7"}
                crypto = CryptoPrimitives()
                message, _ = ts2.signing_message(pins, material["account_key"],
                                                 material["agent_public_key"], action, crypto)
                signature = "0x" + asyncio.run(signer.sign_bytes(message)).hex()
                signed = {"schema": ts2.REQUEST_SCHEMA, **pins,
                          "account_key": material["account_key"],
                          "agent_public_key": signer.public_key, "signature": signature,
                          "action": action}
                self.assertEqual(ts2.request_copy(signed, pins, crypto), signed)
                self.assertEqual(grant, exported["delegation"])
                self.assertEqual(signer.public_key, material["agent_public_key"])
                self.assertNotIn(exported["private_key_seed"][2:], repr(signer))
                self.assertEqual(Path(path).read_bytes(), before)
                self.assertEqual(os.stat(path).st_mode & 0o777, mode)

    def test_v4_key_files_are_refused_by_name(self):
        pins, _ = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            path = write(directory, v4_export())
            with self.assertRaises(AgentError) as caught:
                self.load(path, pins)
            self.assertEqual(caught.exception.code, "v4_key")
            self.assertEqual(str(caught.exception), "TS2: create a new bot key.")

    def test_another_ts2_deployment_is_a_previous_deployment_key(self):
        season1 = {"settlement": "0x3beb98f3ab27d3ac9595e28a3ca03e016fb2745b",
                   "config_hash": "0x6a72cb1998229bcd0d5ce8ea8bbeb0d6c294ee51a385f08208fe3439b58d8d91"}
        for change in ({"settlement": "0x" + "09" * 20}, {"config_hash": "0x" + "11" * 32},
                       season1):
            pins, exported = fixture()
            exported.update(change)
            exported["delegation"]["material"].update(change)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(AgentError) as caught:
                    self.load(write(directory, exported), pins)
                self.assertEqual(caught.exception.code, "previous_deployment")
                self.assertIn("previous testnet deployment", str(caught.exception))

    def test_exact_testnet_labels_grant_and_seed_required(self):
        changes = [
            lambda v: v.update(schema="arena-agent-key-v1"),
            lambda v: v.pop("schema"),
            lambda v: v.update(environment="PRODUCTION"),
            lambda v: v.update(chain_id=1),
            lambda v: v.update(chain_id=True),
            lambda v: v.update(chain_id="421614"),
            lambda v: v.update(settlement="0x" + "00" * 20),
            lambda v: v.update(private_key_seed="0x" + "00" * 31),
            lambda v: v.update(private_key_seed="0x" + "AB" * 32),
            lambda v: v.update(private_key="must not accept a second secret field"),
            lambda v: v.update(public_grant={}),
            lambda v: v["delegation"]["material"].update(can_withdraw=True),
            lambda v: v["delegation"]["material"].update(permissions=16),
            lambda v: v["delegation"]["material"].update(permissions=4),
            lambda v: v["delegation"]["material"].update(agent_public_key="0x01" + "00" * 31),
            lambda v: v["delegation"]["material"].update(max_expiry_ms="1"),
            lambda v: v["delegation"]["material"].update(settlement="0x" + "09" * 20),
            lambda v: v["delegation"].update(signature="0x" + "5a" * 64),
            lambda v: v["delegation"].update(extra=True),
        ]
        for index, change in enumerate(changes):
            pins, exported = fixture()
            change(exported)
            with self.subTest(case=index), tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported)
                before = Path(path).read_bytes()
                with self.assertRaises(AgentError) as caught:
                    self.load(path, pins)
                self.assertNotIn(path, str(caught.exception))
                self.assertNotIn(exported.get("private_key_seed", "0x")[2:] or "-", str(caught.exception))
                self.assertEqual(Path(path).read_bytes(), before)

    def test_seed_must_derive_the_delegated_agent_key(self):
        pins, exported = fixture()
        _, other = fixture()
        exported["private_key_seed"] = other["private_key_seed"]
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            with self.assertRaises(AgentError):
                self.load(write(directory, exported), pins)

    def test_approved_origin_not_discovered_from_file_or_redirectable(self):
        invalid = ("https://other.example/", "http://127.0.0.1/", "https://native-testnet.example",
                   "https://native-testnet.example/path/", "https://user:pass@native-testnet.example/",
                   "https://native-testnet.example/?q=1", "https://native-testnet.example/#x",
                   "https://NATIVE-TESTNET.example/", "https://native-testnet.example:443/",
                   "\nhttps://native-testnet.example/", "https://native-testnet.example/\n")
        for api in invalid:
            pins, exported = fixture()
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                path = write(directory, exported)
                with self.assertRaises(AgentError):
                    self.load(path, pins, api)
            # Even a matching explicit approval cannot make a malformed URL valid.
            if api != "https://other.example/":
                exported["api_url"] = api
                with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                    with self.assertRaises(AgentError):
                        self.load(write(directory, exported), pins, api)
        pins, exported = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            with self.assertRaises(AgentError):
                self.load(write(directory, exported), pins, None)

    def test_installed_pins_cannot_be_replaced_by_the_file_or_caller(self):
        pins, exported = fixture()
        for change in (
            lambda p: p.update(settlement="0x" + "09" * 20),
            lambda p: p.update(config_hash="0x" + "11" * 32),
            lambda p: p.update(chain_id=31337),
            lambda p: p.update(market_id="0x" + "01" * 32),
        ):
            installed = copy.deepcopy(pins)
            change(installed)
            with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
                with self.assertRaises(AgentError):
                    self.load(write(directory, exported), installed)

    def test_legacy_isolated_schema_remains_separate_not_silently_converted(self):
        pins, exported = fixture()
        legacy = {"schema": "fairvenue-isolated-v1-agent-key-v1", "chain_id": 421614,
                  "settlement": ts2.SETTLEMENT,
                  "agent_public_key": exported["delegation"]["material"]["agent_public_key"],
                  "public_grant": {}, "private_key": exported["private_key_seed"]}
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            old_path = write(directory, legacy, name="old.json")
            new_path = write(directory, exported)
            self.load(new_path, pins)
            with self.assertRaises(AgentError):
                self.load(old_path, pins)
            with self.assertRaises(AgentError):
                load_v1_agent_key(new_path, policy=pins)

    def test_private_regular_file_only_without_permission_repair(self):
        pins, exported = fixture()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            path = write(directory, exported)
            link = str(Path(directory).resolve() / "link")
            os.symlink(path, link)
            with self.assertRaises(AgentError):
                self.load(link, pins)
            os.unlink(link)
            os.link(path, link)
            with self.assertRaises(AgentError):
                self.load(path, pins)
            os.unlink(link)
            os.chmod(path, 0o644)
            with self.assertRaises(AgentError):
                self.load(path, pins)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            fifo = str(Path(directory).resolve() / "fifo")
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(AgentError):
                self.load(fifo, pins)

    def test_duplicate_unknown_oversize_and_descriptor_mutation_refused(self):
        pins, exported = fixture()
        raw = json.dumps(exported, separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory(prefix="native-key-offline-") as directory:
            for index, data in enumerate((raw[:-1] + b',"chain_id":421614}', b"{bad", b" " * 16385)):
                with self.assertRaises(AgentError):
                    self.load(write(directory, data, name=f"bad-{index}"), pins)
            path = write(directory, exported)
            original = ts2.grant_copy

            def change_after_read(*args):
                os.chmod(path, 0o400)
                return original(*args)

            with patch.object(ts2, "grant_copy", side_effect=change_after_read), self.assertRaises(AgentError):
                self.load(path, pins)


if __name__ == "__main__":
    unittest.main()
