"""Independently installed public TESTNET deployment pins; never discovers policy."""

from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import dataclass

from .client import _json
from .crypto import CryptoPrimitives
from .key_file import _NATIVE_MOCK_TOKEN, _NATIVE_SETTLEMENT, _identity, _native_api_url, _private
from .protocol import AgentError, encoded_hex, exact, fail, policy_copy


@dataclass(frozen=True, repr=False)
class NativeTestnetInstallation:
    """Public installation only; gives no account, signing or release authority."""

    api_url: str
    sha256: str
    _policy: dict

    @property
    def policy(self):
        return copy.deepcopy(self._policy)

    def __repr__(self):
        return "<NativeTestnetInstallation TESTNET public pins, not live acceptance>"


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
            (
                "schema",
                "environment",
                "settlement_mode",
                "chain_id",
                "settlement",
                "mock_token",
                "api_namespace",
                "api_url",
                "agent_policy",
            ),
        )
        if (
            value["schema"] != "fairvenue-native-testnet-installation-v1"
            or value["environment"] != "TESTNET"
            or value["settlement_mode"] != "mock_token_sp1_operator_beta"
            or type(value["chain_id"]) is not int
            or value["chain_id"] != 421614
            or value["settlement"] != _NATIVE_SETTLEMENT
            or value["mock_token"] != _NATIVE_MOCK_TOKEN
            or value["api_namespace"] != "/v1"
        ):
            fail()
        api = _native_api_url(value["api_url"])
        policy = policy_copy(value["agent_policy"], CryptoPrimitives())
        if encoded_hex(policy["domain"]["settlement"]) != _NATIVE_SETTLEMENT:
            fail()
        if (
            _identity(os.fstat(descriptor)) != _identity(held)
            or _identity(os.lstat(path)) != _identity(held)
            or os.path.realpath(path) != path
        ):
            fail()
        # policy_copy's calculated commitment is not an accepted policy input.
        policy.pop("commitment")
        return NativeTestnetInstallation(api, expected_sha256, policy)
    except Exception:
        raise AgentError("invalid_input") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
