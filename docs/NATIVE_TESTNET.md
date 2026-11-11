# Experimental native TESTNET client

The separate package and complete self-contained installation, no-order dry-run
and exact-original recovery guide are in
[experimental/native-testnet](../experimental/native-testnet/README.md).

From version 0.3.0 the client speaks the TS2 testnet with the same calls as before.
Version 0.4.0 pins TS2 season 2 and adds the signed account read `account()`,
`submit()` with one context read per command and slippage-bounded MARKET orders. It
accepts only season 2 TS2 bot keys (`fairvenue-ts2-testnet-agent-v1`). A V4 key file is
refused with "TS2: create a new bot key" and a TS2 season 1 key as a key of a previous
deployment.

Coding agents can use the same client through the optional MCP server: setup, account
and market data, signed orders within user-set limits and recovery. See
[MCP.md](MCP.md).

This is release preparation, not hosted access, proof acceptance or public GO.
The existing `fairvenue` SDK remains V0-only. The installable skill now routes native TESTNET
to [its self-contained native guide](../skills/fairvenue/references/native-testnet.md), without
converting V0 keys or changing the legacy signing/transport.
