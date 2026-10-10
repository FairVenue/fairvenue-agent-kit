from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "skills/fairvenue/references/native-testnet.md"
POLICY_SHA = "aa622b92aedadb453e09f471dd9ecbfd757c26262cef1ae43c93ad2230181793"


def test_native_skill_recipe_checks_independent_pins_then_signed_context_only(
    monkeypatch: Any, capsys: Any
) -> None:
    calls: list[str] = []
    native = types.ModuleType("fairvenue_schema6")
    installed = types.SimpleNamespace(
        api_url="https://app.fairvenue.xyz/", policy={"synthetic": "independent policy"}
    )

    def load(path: str, *, expected_sha256: str) -> Any:
        assert path == "/private/native-installation.json"
        assert expected_sha256 == POLICY_SHA
        calls.append("installation_pin")
        return installed

    class Client:
        @classmethod
        def from_credentials(cls, path: str, **kwargs: Any) -> Client:
            assert path == "/private/downloaded-testnet-agent.json"
            assert kwargs == {
                "policy": installed.policy,
                "approved_api_url": installed.api_url,
                "custody_path": "/private/dedicated-continuity/original.sqlite",
                "initialize": True,
            }
            calls.append("credential_local_only")
            return cls()

        async def snapshot(self) -> None:
            calls.append("signed_native_context")

        def close(self) -> None:
            calls.append("close")

    native.load_native_testnet_installation = load  # type: ignore[attr-defined]
    native.NativeTestnetClient = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fairvenue_schema6", native)
    snippet = NATIVE.read_text().split("```python\n", 1)[1].split("```", 1)[0]
    exec(compile(snippet, "native-skill-no-order", "exec"), {})  # noqa: S102
    assert calls == [
        "installation_pin",
        "credential_local_only",
        "signed_native_context",
        "close",
    ]
    assert capsys.readouterr().out == "Native TESTNET context verified. No order submitted.\n"


def test_native_skill_bad_installation_stops_before_credential_read(monkeypatch: Any) -> None:
    native = types.ModuleType("fairvenue_schema6")

    def refuse(*_: Any, **__: Any) -> None:
        raise ValueError("synthetic installation refused")

    native.load_native_testnet_installation = refuse  # type: ignore[attr-defined]
    native.NativeTestnetClient = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fairvenue_schema6", native)
    snippet = NATIVE.read_text().split("```python\n", 1)[1].split("```", 1)[0]
    with pytest.raises(ValueError, match="synthetic installation refused"):
        exec(compile(snippet, "native-skill-refusal", "exec"), {})  # noqa: S102
