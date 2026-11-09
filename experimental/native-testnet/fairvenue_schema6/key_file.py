"""Explicit owner-private key export readers; never adopt Arena/V0/V4 or repair permissions."""

from __future__ import annotations

import os
import stat
from urllib.parse import urlsplit

from . import ts2
from .client import _json
from .crypto import CryptoPrimitives, MemorySigner
from .protocol import AgentError, encoded_hex, exact, fail, grant_copy, hex_value, policy_copy
from .transport import HttpTransport

# Codes a caller must see: a V4 file, or a TS2 key of another deployment.
_NAMED = ("v4_key", "previous_deployment", "crypto_unavailable")


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
    if native:
        # A V4 key can never sign TS2 commands. Refuse it by name, never adapt it.
        if type(exported) is dict and exported.get("schema") == ts2.V4_KEY_SCHEMA:
            fail("v4_key")
        pins = ts2.pinned(policy)
        exact(exported, ("schema", "environment", "chain_id", "settlement", "config_hash",
                         "api_url", "delegation", "private_key_seed"))
        if (exported["schema"] != ts2.KEY_SCHEMA or exported["environment"] != "TESTNET"
                or type(exported["chain_id"]) is not int or exported["chain_id"] != ts2.CHAIN_ID):
            fail()
        # A well-formed TS2 key pinned to another settlement or config hash is
        # another deployment's key. Refuse it by name, never migrate it.
        if ts2.deployment({key: exported[key] for key in ("chain_id", "settlement",
                                                          "config_hash")}) != pins:
            fail("previous_deployment")
        if exported["api_url"] != _native_api_url(approved_api_url):
            fail()
        public_grant = ts2.grant_copy(exported["delegation"], pins)
        public_key = public_grant["material"]["agent_public_key"]
        seed_field = "private_key_seed"
    else:
        pinned = policy_copy(policy, primitives)
        exact(exported, ("schema", "chain_id", "settlement", "agent_public_key", "public_grant", "private_key"))
        if (exported["schema"] != "fairvenue-isolated-v1-agent-key-v1"
                or type(exported["chain_id"]) is not int or exported["chain_id"] != 421614
                or hex_value(exported["settlement"], 20) != encoded_hex(pinned["domain"]["settlement"])):
            fail()
        public_grant = grant_copy(exported["public_grant"], pinned, primitives)
        public_key = hex_value(exported["agent_public_key"])
        if public_key != public_grant["agentPublicKey"]:
            fail()
        seed_field = "private_key"
    seed = bytes.fromhex(hex_value(exported[seed_field], nonzero=False)[2:])
    signer = MemorySigner._from_v1_seed(seed)
    if signer.public_key != public_key:
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
    except AgentError as error:
        raise AgentError(error.code if error.code in _NAMED else "invalid_input") from None
    except Exception:
        raise AgentError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_v1_agent_key(path, *, policy):
    """Read only the original isolated V1 format; never adopts Arena/V0 keys."""
    return _load_key(path, policy=policy)


def load_native_testnet_agent_key(path, *, policy, approved_api_url):
    """Read a TS2 bot key (``fairvenue-ts2-testnet-agent-v1``): signer and public grant.

    ``policy`` is the installed TS2 deployment pins (chain, settlement and
    config hash) and the HTTPS API URL an independently approved installation
    input, never discovered from this file. A V4 ``fairvenue-native-testnet-agent-v1``
    file raises ``AgentError("v4_key")`` and a TS2 key of another deployment
    ``AgentError("previous_deployment")``. No request, activation or funding occurs.
    """
    return _load_key(path, policy=policy, approved_api_url=approved_api_url, native=True)
