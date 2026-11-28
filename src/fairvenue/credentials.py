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


def _check_directory_stat(stat: os.stat_result) -> None:
    if stat.st_uid != os.getuid() or stat.st_mode & 0o022:
        raise CredentialsError(
            "credential directory must be yours and not writable by group or others"
        )


def check_credential_directory(path: str | os.PathLike[str]) -> None:
    """Refuse an existing directory where another user could replace the credential file."""
    if os.name == "posix" and Path(path).exists():
        _check_directory_stat(Path(path).stat())


def _write_exclusive(path: Path, flags: int, payload: str, directory: int | None) -> None:
    try:
        descriptor = os.open(path, flags, 0o600, dir_fd=directory)
    except FileExistsError as error:
        raise CredentialsError("credential file already exists") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
    except BaseException:
        os.unlink(path, dir_fd=directory)
        raise


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
        try:
            descriptor = os.open(credential_path, os.O_RDONLY)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise CredentialsError("credential file could not be read") from error
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            if os.name == "posix" and os.fstat(handle.fileno()).st_mode & 0o077:
                raise CredentialsError("credential file must not be accessible by group or others")
            try:
                raw = json.loads(handle.read())
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
        parent = credential_path.parent
        created = not parent.exists()
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if os.name != "posix":
            _write_exclusive(credential_path, flags, payload, None)
            return
        directory = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | (os.O_NOFOLLOW if created else 0)
        )
        try:
            if created:
                os.fchmod(directory, 0o700)
            _check_directory_stat(os.fstat(directory))
            _write_exclusive(Path(credential_path.name), flags | os.O_NOFOLLOW, payload, directory)
        finally:
            os.close(directory)

    def redacted(self) -> dict[str, Any]:
        return {
            "api_url": self.api_url,
            "environment": self.environment,
            "account_id": self.account_id,
            "agent_key_id": self.agent_key_id,
        }
