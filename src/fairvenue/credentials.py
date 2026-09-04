"""Owner-only FairVenue agent credentials."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import CredentialsError

_ACCOUNT = re.compile(r"^acc_[a-z0-9]{1,64}$")
_AGENT = re.compile(r"^agent_[a-z0-9]{1,64}$")
_SEED = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Credentials:
    """Account identifiers and a local Ed25519 seed."""

    api_url: str
    environment: str
    account_id: str
    agent_key_id: str
    private_key_seed: str = field(repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CredentialsError("api_url must be an absolute HTTP(S) URL")
        if self.environment not in {"testnet", "mainnet"}:
            raise CredentialsError("environment must be testnet or mainnet")
        if not _ACCOUNT.fullmatch(self.account_id):
            raise CredentialsError("account_id is invalid")
        if not _AGENT.fullmatch(self.agent_key_id):
            raise CredentialsError("agent_key_id is invalid")
        if not _SEED.fullmatch(self.private_key_seed):
            raise CredentialsError("private_key_seed must contain 32 lowercase hex bytes")

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> Credentials:
        credential_path = Path(path)
        stat = credential_path.stat()
        if os.name == "posix" and stat.st_mode & 0o077:
            raise CredentialsError("credential file must not be accessible by group or others")
        try:
            raw = json.loads(credential_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CredentialsError("credential file could not be read") from error
        if not isinstance(raw, dict) or set(raw) != {
            "api_url",
            "environment",
            "account_id",
            "agent_key_id",
            "private_key_seed",
        }:
            raise CredentialsError("credential file has an unexpected schema")
        return cls(**raw)

    def write_new(self, path: str | os.PathLike[str]) -> None:
        credential_path = Path(path)
        credential_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            credential_path.parent.chmod(0o700)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        try:
            descriptor = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise CredentialsError("credential file already exists") from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(payload)
        except BaseException:
            credential_path.unlink(missing_ok=True)
            raise

    def redacted(self) -> dict[str, Any]:
        return {
            "api_url": self.api_url,
            "environment": self.environment,
            "account_id": self.account_id,
            "agent_key_id": self.agent_key_id,
        }
