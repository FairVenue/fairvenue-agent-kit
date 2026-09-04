# API decisions that matter

Use only the FairVenue-native trading surface:

| Surface | SDK entry | Boundary |
| --- | --- | --- |
| `POST /api/v1/info` | `client.info({...})` | Public reads or authenticated private reads |
| `POST /api/v1/auth` | SDK challenge/token handling | Private-read token, not trade authority |
| `POST /api/v1/exchange` | `client.submit(action)` | JCS/Ed25519-signed commands |
| `GET /api/v1/ws` | `FairVenueStream(client).events(...)` | Subscribe; reconcile snapshots/deltas |

Private reads include `account_state`, `open_orders`, `order_status`, `historical_orders`, `fills`,
`positions`, `funding_payments`, `transaction_status`. The SDK handles their auth challenge. Never
print bearer tokens, signed envelopes, raw private responses or private stream events.

## Orders

`client.place_limit` accepts `market_id`, `side` (`buy`/`sell`), decimal `price` and `size`,
`time_in_force` (`Alo`/`Ioc`/`Gtc`), `reduce_only` and `client_order_id`.

- `Alo`: post-only. Crossing orders are rejected; do not silently retry as taker.
- `Ioc`: execute within the protection price, cancel the unfilled remainder; a position may remain.
- `Gtc`: the unfilled remainder can rest. Do not use it as an implicit market order.
- `client.protected_ioc` derives a protection price from BBO plus `max_slippage_bps` and rounds
  conservatively to the market tick. Reject unavailable/stale BBO before submission.
- Generate a stable `client_order_id` once per logical order (`0x` + 32 lowercase hex characters).
  Retries reuse that client ID but need a fresh signed envelope and unique nonce.
- `client.wait_for_transaction(accepted["response"]["tx_hash"])` returns the eventual transaction
  result. `applied` still does not mean every child order filled; inspect order/fill results.

For continuous strategies, explicitly configure and refresh `schedule_cancel`, then cancel on
shutdown. The SDK exposes generic signed actions; inspect the public contract before inventing
fields for cancels, modifies, leverage, fee-mode changes or the dead man's switch.

## Signing and streams

The SDK signs RFC 8785 canonical action bytes in an Ed25519 domain containing API version,
environment, account, agent, nonce and expiry. A per-process nonce manager is not coordination
between processes: use separate agent keys or a single coordinated signer. Never reuse nonces.

Public depth subscription: `{"type":"l2_book","market_id":0,"depth":50}`.
The first depth event is a snapshot. A delta's `start_sequence` must equal the previous
`end_sequence + 1`. On any gap or disconnect, discard the book, reconnect/resubscribe and wait for
a fresh snapshot before trading. The included example stops safely on a gap; it is not a complete
book reconstruction engine. Generated integrations must implement their own recovery state.

Public `index_candles` reflect reference mid-price, not necessarily executed FairVenue trades.
Unsigned settlement candidates and proof metadata are not evidence of onchain settlement.

## Deeper public references

Open only those needed for the integration, at the same reviewed SDK commit where possible:

- [API overview](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/API_V1.md)
- [Signing and nonce contract](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/SIGNING.md)
- [WebSocket contract](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/WEBSOCKET.md)
- [SDK action helpers](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/src/fairvenue/client.py)
- [Cross-language signing vector](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/tests/fixtures/signing/order.json)

If these sources do not define a needed field or behavior, flag the gap and use a mock or stop that
part of the integration. Do not guess a Binance/Hyperliquid-compatible endpoint.
