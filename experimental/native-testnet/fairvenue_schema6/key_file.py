"""Explicit owner-private V1 export reader; never adopts Arena/V0 or repairs permissions."""

from __future__ import annotations

import os
import stat
from urllib.parse import urlsplit

from .client import _json
from .crypto import CryptoPrimitives, MemorySigner
from .protocol import AgentError, encoded_hex, exact, fail, grant_copy, hex_value, policy_copy
from .transport import HttpTransport

_NATIVE_SCHEMA = "fairvenue-native-testnet-agent-v1"
_NATIVE_SETTLEMENT = "0xaaebec860fc2ec7f77379ac3c4b892266a76bcbf"
_NATIVE_MOCK_TOKEN = "0x2b62f3c0663d9590113e24ec588018bcb81a7d29"


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) in (0o400, 0o600)
            and 0 < info.st_size <= 16384)


def _native_api_url(value):
    # Match the browser's canonical HTTPS origin plus '/'. This constructs no
    # connection, and never lets the credential file select an unapproved host.
    if (type(value) is not str or not 0 < len(value) <= 257
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        fail()
    url = urlsplit(value)
    if (url.scheme != "https" or url.path != "/" or url.query or url.fragment
            or url.username is not None or url.password is not None or url.port == 443):
        fail()
    HttpTransport(value[:-1])  # Existing fixed-origin validation; no HTTP I/O.
    return value


def _material(exported, *, policy, approved_api_url, native):
    primitives = CryptoPrimitives()
    pinned = policy_copy(policy, primitives)
    if native:
        exact(exported, ("schema", "environment", "settlement_mode", "chain_id",
                         "settlement", "mock_token", "api_namespace", "api_url",
                         "agent_policy", "public_grant", "private_key_seed"))
        if (exported["schema"] != _NATIVE_SCHEMA or exported["environment"] != "TESTNET"
                or exported["settlement_mode"] != "mock_token_sp1_operator_beta"
                or type(exported["chain_id"]) is not int or exported["chain_id"] != 421614
                or exported["settlement"] != _NATIVE_SETTLEMENT
                or exported["mock_token"] != _NATIVE_MOCK_TOKEN
                or exported["api_namespace"] != "/v1"
                or exported["api_url"] != _native_api_url(approved_api_url)
                or encoded_hex(pinned["domain"]["settlement"]) != _NATIVE_SETTLEMENT
                or policy_copy(exported["agent_policy"], primitives) != pinned):
            fail()
        public_grant = grant_copy(exported["public_grant"], pinned, primitives)
        public_key = public_grant["agentPublicKey"]
        seed_field = "private_key_seed"
    else:
        exact(exported, ("schema", "chain_id", "settlement", "agent_public_key", "public_grant", "private_key"))
        if (exported["schema"] != "fairvenue-isolated-v1-agent-key-v1"
                or type(exported["chain_id"]) is not int or exported["chain_id"] != 421614
                or hex_value(exported["settlement"], 20) != encoded_hex(pinned["domain"]["settlement"])):
            fail()
        public_grant = grant_copy(exported["public_grant"], pinned, primitives)
        public_key = hex_value(exported["agent_public_key"])
        seed_field = "private_key"
    seed = bytes.fromhex(hex_value(exported[seed_field], nonzero=False)[2:])
    signer = MemorySigner._from_v1_seed(seed)
    if signer.public_key != public_key or signer.public_key != public_grant["agentPublicKey"]:
        fail()
    return signer, public_grant


def _load_key(path, *, policy, approved_api_url=None, native=False):
    descriptor = None
    try:
        if (type(path) is not str or not os.path.isabs(path) or os.path.normpath(path) != path
                or os.path.realpath(path) != path or not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_NONBLOCK") or not hasattr(os, "getuid")):
            fail()
        before = os.lstat(path)
        if not _private(before):
            fail()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        held = os.fstat(descriptor)
        if not _private(held) or _identity(held) != _identity(before):
            fail()
        pieces = []
        size = 0
        while size <= 16384:
            piece = os.read(descriptor, min(4096, 16385 - size))
            if not piece:
                break
            pieces.append(piece)
            size += len(piece)
        if size != held.st_size or size > 16384:
            fail()
        if (_identity(os.fstat(descriptor)) != _identity(held)
                or _identity(os.lstat(path)) != _identity(held) or os.path.realpath(path) != path):
            fail()
        exported = _json(b"".join(pieces))
        signer, public_grant = _material(exported, policy=policy,
                                        approved_api_url=approved_api_url, native=native)
        if (_identity(os.fstat(descriptor)) != _identity(held)
                or _identity(os.lstat(path)) != _identity(held) or os.path.realpath(path) != path):
            fail()
        return signer, public_grant
    except Exception:
        raise AgentError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_v1_agent_key(path, *, policy):
    """Read only the original isolated V1 format; never adopts Arena/V0 keys."""
    return _load_key(path, policy=policy)


def load_native_testnet_agent_key(path, *, policy, approved_api_url):
    """Read the deployed native TESTNET format, returning only signer and public grant.

    Policy and HTTPS API URL are independently approved installation inputs,
    not discovered from this file. No request, activation or funding occurs.
    """
    return _load_key(path, policy=policy, approved_api_url=approved_api_url, native=True)
