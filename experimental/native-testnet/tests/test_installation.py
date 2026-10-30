"""Offline public installation pins; synthetic keys only, no hosted acceptance."""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fairvenue_schema6 import AgentError, NativeTestnetClient, load_native_testnet_installation
from fairvenue_schema6 import installation as module
from test_native_credentials import fixture, write

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "fairvenue_schema6/deployments/arbitrum-sepolia-1k.json"
PUBLIC_SHA = "9a39af143e67f4675f05998bedbafb02a052819d23a2129674db8f8588938d4d"


class InstallationTests(unittest.TestCase):
    def document(self, policy, exported):
        return {
            "schema": "fairvenue-native-testnet-installation-v1",
            **{
                key: exported[key]
                for key in (
                    "environment",
                    "settlement_mode",
                    "chain_id",
                    "settlement",
                    "mock_token",
                    "api_namespace",
                    "api_url",
                )
            },
            "agent_policy": policy,
        }

    def load(self, path, expected=None):
        return load_native_testnet_installation(
            path, expected_sha256=expected or hashlib.sha256(Path(path).read_bytes()).hexdigest()
        )

    def test_published_policy_hash_pins_and_copy_isolation_without_http(self):
        self.assertEqual(hashlib.sha256(PUBLIC.read_bytes()).hexdigest(), PUBLIC_SHA)
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, PUBLIC.read_bytes(), name="installation.json")
            with patch(
                "fairvenue_schema6.transport._http", side_effect=AssertionError("no network")
            ):
                installed = self.load(path, PUBLIC_SHA)
            self.assertEqual(installed.api_url, "https://app.fairvenue.xyz/")
            policy = installed.policy
            self.assertEqual(policy["epoch_ms"], "10")
            self.assertEqual(policy["config_version"], "3")
            policy["domain"]["chain_id"] = 1
            self.assertEqual(installed.policy["domain"]["chain_id"], 421614)
            self.assertNotIn("commitment", installed.policy)

    def test_export_and_independently_pinned_policy_construct_native_client_not_v0(self):
        policy, exported = fixture()
        with tempfile.TemporaryDirectory() as directory:
            key = write(directory, exported)
            path = write(directory, self.document(policy, exported), name="installation.json")
            installed = self.load(path)
            continuity = Path(directory).resolve() / "continuity"
            continuity.mkdir(mode=0o700)
            with patch(
                "fairvenue_schema6.transport._http", side_effect=AssertionError("no network")
            ):
                client = NativeTestnetClient.from_credentials(
                    key,
                    policy=installed.policy,
                    approved_api_url=installed.api_url,
                    custody_path=str(continuity / "original.sqlite"),
                    initialize=True,
                )
            try:
                self.assertFalse(client.has_pending_request())
                self.assertNotIn(exported["private_key_seed"], repr(installed))
            finally:
                client.close()
            self.assertEqual(
                Path(key).read_bytes(), json.dumps(exported, separators=(",", ":")).encode()
            )

    def test_hash_domain_labels_private_fields_url_and_policy_reject(self):
        policy, exported = fixture()
        original = self.document(policy, exported)
        changes = [
            ("schema", "arena-key-v1"),
            ("chain_id", 1),
            ("chain_id", True),
            ("environment", "PRODUCTION"),
            ("settlement_mode", "virtual"),
            ("settlement", "0x" + "0" * 40),
            ("mock_token", "0x" + "1" * 40),
            ("api_namespace", "/api/v1"),
            ("api_url", "https://name:secret@host.example/"),
            ("private_key_seed", "not allowed"),
        ]
        for field, value in changes:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = write(directory, {**original, field: value}, name="installation.json")
                with self.assertRaises(AgentError) as caught:
                    self.load(path)
                self.assertEqual(caught.exception.code, "invalid_input")
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, original, name="installation.json")
            for expected in ["0" * 64, "f" * 64, "F" * 64, "", None]:
                with self.subTest(hash=expected), self.assertRaises(AgentError):
                    load_native_testnet_installation(path, expected_sha256=expected)
            changed = copy.deepcopy(original)
            changed["agent_policy"]["domain"]["settlement"] = [1] * 20
            other = write(directory, changed, name="foreign.json")
            with self.assertRaises(AgentError):
                self.load(other)

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
            validate = module.policy_copy

            def replace(value, crypto):
                raw = path.read_bytes()
                path.unlink()
                path.write_bytes(raw)
                path.chmod(0o600)
                return validate(value, crypto)

            with (
                patch.object(module, "policy_copy", side_effect=replace),
                self.assertRaises(AgentError),
            ):
                self.load(str(path), PUBLIC_SHA)


if __name__ == "__main__":
    unittest.main()
