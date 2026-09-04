# WebSocket

Connect to `/api/v1/ws`, optionally authenticate with a short-lived read token, then subscribe:

```json
{"method":"subscribe","subscription":{"type":"l2_book","market_id":0,"depth":50}}
```

Public channels include `l2_book`, `bbo`, `trades`, `market_context`, `candles`, `index_candles` and
`system_status`. Private channels include `tx_updates`, `order_updates`, `user_fills`, `user_state`,
`positions`, `funding_updates` and `fairlag_updates`.

The first book event is a snapshot. Every delta must start at the previous end sequence plus one.
After a gap, discard local state and reconnect/resubscribe for a new snapshot. A slow consumer is
disconnected rather than allowed to continue silently with stale state.

