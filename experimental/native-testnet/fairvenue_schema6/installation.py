"""Independently installed public TS2 TESTNET deployment pins; never discovers them."""

from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import dataclass

from . import ts2
from .client import _json
from .key_file import _identity, _native_api_url, _private
from .protocol import AgentError, exact, fail

SCHEMA = "fairvenue-ts2-testnet-installation-v1"
#: The packaged pin file of the TS2 deployment, next to this module.
PACKAGED = "deployments/arbitrum-sepolia-ts2.json"


@dataclass(frozen=True, repr=False)
class NativeTestnetInstallation:
    """Public installation only; gives no account, signing or release authority.

    ``policy`` is the TS2 deployment pins ``{chain_id, settlement, config_hash}``
    that ``NativeTestnetClient.from_credentials`` and the key loader expect.
    """

    api_url: str
    sha256: str
    _policy: dict

    @property
    def policy(self):
        return copy.deepcopy(self._policy)

    @property
    def settlement(self):
        return self._policy["settlement"]

    @property
    def config_hash(self):
        return self._policy["config_hash"]

    def __repr__(self):
        return "<NativeTestnetInstallation TS2 TESTNET public pins, not live acceptance>"


def load_native_testnet_installation(path, *, expected_sha256):
    """Read a separately approved owner-private public pin file without HTTP.

    Obtain the SHA256 and approval independently of the downloaded agent file
    and API responses. Never calculate a replacement expected hash to accept
    an unexplained file. Credentials cannot select these deployment pins.
    """
    descriptor = None
    try:
        if (
            type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or expected_sha256 == "0" * 64
            or type(path) is not str
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
            or os.path.realpath(path) != path
        ):
            fail()
        before = os.lstat(path)
        if not _private(before):
            fail()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        held = os.fstat(descriptor)
        if not _private(held) or _identity(held) != _identity(before):
            fail()
        data = bytearray()
        while len(data) <= 16384:
            piece = os.read(descriptor, min(4096, 16385 - len(data)))
            if not piece:
                break
            data.extend(piece)
        if (
            len(data) != held.st_size
            or len(data) > 16384
            or hashlib.sha256(data).hexdigest() != expected_sha256
        ):
            fail()
        value = _json(bytes(data))
        exact(
            value,
            ("schema", "environment", "chain_id", "settlement", "config_hash", "api_namespace",
             "api_url"),
        )
        if (
            value["schema"] != SCHEMA
            or value["environment"] != "TESTNET"
            or value["api_namespace"] != "/v1"
        ):
            fail()
        pins = ts2.pinned({key: value[key] for key in ("chain_id", "settlement", "config_hash")})
        api = _native_api_url(value["api_url"])
        if (
            _identity(os.fstat(descriptor)) != _identity(held)
            or _identity(os.lstat(path)) != _identity(held)
            or os.path.realpath(path) != path
        ):
            fail()
        return NativeTestnetInstallation(api, expected_sha256, pins)
    except Exception:
        raise AgentError("invalid_input") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
