from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from fairvenue import Credentials
from fairvenue.errors import CredentialsError


def credentials() -> Credentials:
    return Credentials(
        api_url="http://127.0.0.1:8088",
        environment="testnet",
        account_id="acc_example1",
        agent_key_id="agent_example1",
        private_key_seed="07" * 32,
    )


def test_writes_owner_only_file_without_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "private" / "testnet.json"
    credentials().write_new(destination)

    assert Credentials.from_file(destination) == credentials()
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
        assert destination.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(CredentialsError, match="already exists"):
        credentials().write_new(destination)

    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    credentials().write_new(shared / "testnet.json")
    if os.name == "posix":
        assert shared.stat().st_mode & 0o777 == 0o755
        writable = tmp_path / "writable"
        writable.mkdir()
        writable.chmod(0o777)
        with pytest.raises(CredentialsError, match="not writable by group or others"):
            credentials().write_new(writable / "testnet.json")
        assert not (writable / "testnet.json").exists()
        link = tmp_path / "link.json"
        link.symlink_to(destination)
        assert Credentials.from_file(link) == credentials()


def test_rejects_loose_permissions(tmp_path: Path) -> None:
    destination = tmp_path / "testnet.json"
    destination.write_text(json.dumps(credentials().redacted()), encoding="utf-8")
    destination.chmod(0o644)

    with pytest.raises(CredentialsError, match="group or others"):
        Credentials.from_file(destination)


def test_credentials_repr_does_not_expose_the_seed() -> None:
    value = credentials()
    assert value.private_key_seed not in repr(value)
    assert value.private_key_seed not in str(value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permissions")
def test_init_local_refuses_an_unsafe_directory_before_any_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fairvenue import cli

    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("init-local reached the network")

    monkeypatch.setattr(cli.httpx, "Client", no_network)
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    args = argparse.Namespace(
        base_url="http://127.0.0.1:8088",
        credentials=str(shared / "testnet.json"),
        virtual_usdc="100",
    )
    with pytest.raises(CredentialsError, match="not writable by group or others"):
        cli.init_local(args)
