"""RFC 8785 and Ed25519 signing for FairVenue API v1."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import rfc8785
from nacl.signing import SigningKey

API_VERSION = "v1"


def canonical_action(action: Mapping[str, Any]) -> str:
    return rfc8785.dumps(dict(action)).decode("utf-8")


def signing_message(
    *,
    environment: str,
    account_id: str,
    agent_key_id: str,
    nonce: int,
    expires_after: int,
    action: Mapping[str, Any],
) -> bytes:
    return (
        "FAIRVENUE\n"
        f"api_version:{API_VERSION}\n"
        f"environment:{environment}\n"
        f"account_id:{account_id}\n"
        f"agent_key_id:{agent_key_id}\n"
        f"nonce:{nonce}\n"
        f"expires_after:{expires_after}\n"
        f"action:{canonical_action(action)}"
    ).encode()


def auth_message(
    *,
    environment: str,
    account_id: str,
    agent_key_id: str,
    challenge: str,
    expires_at: int,
) -> bytes:
    return (
        "FAIRVENUE\n"
        f"api_version:{API_VERSION}\n"
        f"environment:{environment}\n"
        "purpose:authenticate\n"
        f"account_id:{account_id}\n"
        f"agent_key_id:{agent_key_id}\n"
        f"challenge:{challenge}\n"
        f"expires_at:{expires_at}"
    ).encode()


def sign(seed_hex: str, message: bytes) -> str:
    signature = SigningKey(bytes.fromhex(seed_hex)).sign(message).signature
    return f"0x{signature.hex()}"


def public_key(seed_hex: str) -> str:
    return f"0x{SigningKey(bytes.fromhex(seed_hex)).verify_key.encode().hex()}"
