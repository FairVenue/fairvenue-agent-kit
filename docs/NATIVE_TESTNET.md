# Experimental native TESTNET client

The separate package and complete self-contained installation, no-order dry-run
and exact-original recovery guide are in
[experimental/native-testnet](../experimental/native-testnet/README.md).

From version 0.3.0 the client speaks the TS2 testnet with the same calls as before.
It accepts only TS2 bot keys (`fairvenue-ts2-testnet-agent-v1`). A V4 key file is
refused with "TS2: create a new bot key".

Coding agents can use the same client through the optional MCP server: setup,
market data, signed orders within user-set limits and recovery. See
[MCP.md](MCP.md).

This is release preparation, not hosted access, proof acceptance or public GO.
The existing `fairvenue` SDK remains V0-only. The installable skill now routes native TESTNET
to [its self-contained native guide](../skills/fairvenue/references/native-testnet.md), without
converting V0 keys or changing the legacy signing/transport.
