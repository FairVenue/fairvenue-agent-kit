"""Offline public TS2 installation pins; synthetic keys only, no hosted acceptance."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fairvenue_schema6 import AgentError, NativeTestnetClient, load_native_testnet_installation, ts2
from fairvenue_schema6 import installation as module
from test_native_credentials import API_URL, fixture, write

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "fairvenue_schema6" / module.PACKAGED
PUBLIC_SHA = "ba25da81e3ebc9f6679a184993dbc4a64fd089684f54e174e8ad654b725232d4"
#: TS2 season 1, now another deployment: its pins never install.
SEASON1 = {"settlement": "0x3beb98f3ab27d3ac9595e28a3ca03e016fb2745b",
           "config_hash": "0x6a72cb1998229bcd0d5ce8ea8bbeb0d6c294ee51a385f08208fe3439b58d8d91"}


class InstallationTests(unittest.TestCase):
    def document(self, **changes):
        return {"schema": module.SCHEMA, "environment": "TESTNET", **ts2.PINS,
                "api_namespace": "/v1", "api_url": API_URL, **changes}

    def load(self, path, expected=None):
        return load_native_testnet_installation(
            path, expected_sha256=expected or hashlib.sha256(Path(path).read_bytes()).hexdigest()
        )

    def test_published_pins_hash_and_copy_isolation_without_http(self):
        self.assertEqual(hashlib.sha256(PUBLIC.read_bytes()).hexdigest(), PUBLIC_SHA)
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, PUBLIC.read_bytes(), name="installation.json")
            with patch("fairvenue_schema6.transport._http", side_effect=AssertionError("no network")):
                installed = self.load(path, PUBLIC_SHA)
            self.assertEqual(installed.api_url, "https://app.fairvenue.xyz/")
            self.assertEqual(installed.policy, ts2.PINS)
            self.assertEqual(installed.settlement, "0xfd5e17a1a722e0890669aa8c71a024ea4e4277bd")
            self.assertEqual(installed.config_hash,
                             "0x416ca6d7eeae85857f387b55e829052eb6474065874774f299e09c0484364bbd")
            policy = installed.policy
            policy["chain_id"] = 1
            self.assertEqual(installed.policy["chain_id"], 421614)
            self.assertNotIn("TESTNET public pins", repr(policy))

    def test_export_and_independently_pinned_installation_construct_the_ts2_client(self):
        _, exported = fixture()
        with tempfile.TemporaryDirectory() as directory:
            key = write(directory, exported)
            installed = self.load(write(directory, self.document(), name="installation.json"))
            continuity = Path(directory).resolve() / "continuity"
            continuity.mkdir(mode=0o700)
            with patch("fairvenue_schema6.transport._http", side_effect=AssertionError("no network")):
                client = NativeTestnetClient.from_credentials(
                    key, policy=installed.policy, approved_api_url=installed.api_url,
                    custody_path=str(continuity / "original.sqlite"), initialize=True,
                )
            try:
                self.assertFalse(client.has_pending_request())
                self.assertEqual(client.settlement, ts2.SETTLEMENT)
                self.assertNotIn(exported["private_key_seed"], repr(installed) + repr(client))
            finally:
                client.close()
            self.assertEqual(Path(key).read_bytes(), json.dumps(exported, separators=(",", ":")).encode())

    def test_hash_domain_labels_private_fields_url_and_v4_pins_reject(self):
        changes = [
            {"schema": "fairvenue-native-testnet-installation-v1"},
            {"chain_id": 1}, {"chain_id": True}, {"environment": "PRODUCTION"},
            {"settlement": "0x" + "0" * 40}, {"settlement": "0xaaebec860fc2ec7f77379ac3c4b892266a76bcbf"},
            SEASON1, {"settlement": SEASON1["settlement"]}, {"config_hash": SEASON1["config_hash"]},
            {"config_hash": "0x" + "11" * 32}, {"api_namespace": "/api/v1"},
            {"api_url": "https://name:secret@host.example/"}, {"private_key_seed": "not allowed"},
            {"agent_policy": {}},
        ]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                path = write(directory, self.document(**change), name="installation.json")
                with self.assertRaises(AgentError) as caught:
                    self.load(path)
                self.assertEqual(caught.exception.code, "invalid_input")
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, self.document(), name="installation.json")
            for expected in ["0" * 64, "f" * 64, "F" * 64, "", None]:
                with self.subTest(hash=expected), self.assertRaises(AgentError):
                    load_native_testnet_installation(path, expected_sha256=expected)

    def test_public_mode_symlink_hardlink_fifo_and_oversize_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(write(directory, PUBLIC.read_bytes(), name="installation.json"))
            for mode in [0o644, 0o640, 0o660]:
                path.chmod(mode)
                with self.subTest(mode=mode), self.assertRaises(AgentError):
                    self.load(str(path), PUBLIC_SHA)
            path.chmod(0o600)
            hard = path.with_name("hard.json")
            os.link(path, hard)
            with self.assertRaises(AgentError):
                self.load(str(path), PUBLIC_SHA)
            hard.unlink()
            alias = path.with_name("alias.json")
            alias.symlink_to(path)
            with self.assertRaises(AgentError):
                self.load(str(alias), PUBLIC_SHA)
            fifo = path.with_name("fifo")
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(AgentError):
                self.load(str(fifo), PUBLIC_SHA)
            large = write(directory, b"x" * 16385, name="large.json")
            with self.assertRaises(AgentError):
                self.load(large)

    def test_same_byte_replacement_during_validation_refuses_held_fd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(write(directory, PUBLIC.read_bytes(), name="installation.json"))
            validate = module.ts2.pinned

            def replace(value):
                raw = path.read_bytes()
                path.unlink()
                path.write_bytes(raw)
                path.chmod(0o600)
                return validate(value)

            with patch.object(module.ts2, "pinned", side_effect=replace), self.assertRaises(AgentError):
                self.load(str(path), PUBLIC_SHA)


if __name__ == "__main__":
    unittest.main()
