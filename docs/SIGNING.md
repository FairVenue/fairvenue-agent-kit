# Signing

The SDK canonicalizes only the action with RFC 8785 JCS and signs the complete domain-separated
message with Ed25519:

```text
FAIRVENUE
api_version:v1
environment:testnet
account_id:acc_...
agent_key_id:agent_...
nonce:1786800000123
expires_after:1786800005123
action:{"orders":[...],"type":"order"}
```

The signature is lowercase `0x` hex. The environment prevents testnet signatures from being valid
on a future mainnet. The checked-in test fixture must match the Rust server fixture byte-for-byte.

Use `max(current_time_ms, previous_nonce + 1)`. The server retains a durable sliding window of 256
values per agent key. A process may submit unique out-of-order values inside the window, but a
nonce can never be reused.

Private reads use a challenge signed with a separate `purpose:authenticate` message. The resulting
short-lived bearer token cannot authorize trading.

