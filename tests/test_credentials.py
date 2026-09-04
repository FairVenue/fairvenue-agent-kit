from __future__ import annotations

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
    with pytest.raises(CredentialsError, match="already exists"):
        credentials().write_new(destination)


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
