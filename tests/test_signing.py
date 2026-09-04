from __future__ import annotations

import json
from pathlib import Path

from nacl.signing import VerifyKey

from fairvenue.signing import canonical_action, sign, signing_message


def test_matches_rust_cross_language_vector() -> None:
    fixture = json.loads(Path("tests/fixtures/signing/order.json").read_text(encoding="utf-8"))
    message = signing_message(
        environment="testnet",
        account_id=fixture["account_id"],
        agent_key_id=fixture["agent_key_id"],
        nonce=fixture["nonce"],
        expires_after=fixture["expires_after"],
        action=fixture["action"],
    )
    signature = sign("07" * 32, message)

    assert canonical_action(fixture["action"]) == (
        '{"orders":[{"client_order_id":"0x1234567890abcdef1234567890abcdef",'
        '"market_id":0,"price":"100000.0","reduce_only":false,"side":"buy",'
        '"size":"0.0100","time_in_force":"Alo"}],"type":"order"}'
    )
    assert signature == fixture["signature"]
    VerifyKey(
        bytes.fromhex("ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c")
    ).verify(message, bytes.fromhex(signature[2:]))
