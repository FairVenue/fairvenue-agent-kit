"""Exact imported source and public synthetic fixtures; no hosted acceptance."""

import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ImportedSourceIntegrityTests(unittest.TestCase):
    def test_imported_protocol_sources_metadata_and_vectors_are_byte_identical(self):
        manifest = json.loads((ROOT / "SOURCE.json").read_text())
        self.assertEqual(manifest["schema"], "fairvenue-native-testnet-source-parity-v1")
        for entry in [*manifest["byte_identical"], *manifest["public_extensions"]]:
            with self.subTest(path=entry["path"]):
                relative = Path(entry["path"])
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), entry["sha256"]
                )
        modules = {entry["path"] for entry in manifest["byte_identical"]}
        self.assertIn("fairvenue_schema6/native_testnet.py", modules)
        self.assertIn("fairvenue_schema6/protocol.py", modules)
        self.assertIn("requirements.txt", modules)
        self.assertEqual(
            {entry["path"] for entry in manifest["public_extensions"]},
            {
                "pyproject.toml",
                "fairvenue_schema6/__init__.py",
                "fairvenue_schema6/installation.py",
                "fairvenue_schema6/deployments/arbitrum-sepolia-1k.json",
            },
        )

    def test_native_package_does_not_depend_on_an_external_fixture_checkout(self):
        for test in (ROOT / "tests").glob("test_*.py"):
            self.assertNotIn('ROOT.parent / "' + "web/", test.read_text())
        for name in ("agent-commands.golden.json", "agent-context.golden.json"):
            vector = json.loads((ROOT / "tests/fixtures" / name).read_text())
            if name == "agent-commands.golden.json":
                self.assertEqual(
                    vector["label"], "synthetic-public-only-native-signing-parity-no-proof"
                )
            else:
                self.assertEqual(vector["schema"], "fairvenue-v1-agent-context-golden-v1")
            self.assertNotIn("private_key_seed", vector)


if __name__ == "__main__":
    unittest.main()
