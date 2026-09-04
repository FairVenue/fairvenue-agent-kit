# FairVenue Python engineering rules

- This SDK targets the FairVenue Arena virtual-settlement testnet. Never route user orders or
  credentials to Binance or another external venue.
- Keep monetary values as decimal strings or `Decimal`; reject binary floats in API payloads.
- Preserve RFC 8785 JCS, Ed25519 domain separation, unique per-agent nonces and short expiries.
- Never log private keys, bearer tokens, complete signed envelopes or private stream payloads.
- Local credential files must be owner-only and must never be committed.
- Treat `accepted` as durable receipt only, never as an open or filled order.
- No unbounded market-order abstraction. Taker helpers must submit a price-protected IOC.
- Book consumers must discard state and resubscribe after a sequence gap.
- Keep the checked-in signing vector compatible with the Rust server fixture.
- Before handoff run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`, and
  `python -m build`.

