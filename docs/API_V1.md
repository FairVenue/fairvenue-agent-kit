# API v1 summary

All requests are JSON and unknown fields are rejected. The body limit is 64 KiB. Monetary values
are decimal strings. Market `0` is the virtual BTCUSDC perpetual.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/info` | Public and bearer-authenticated reads |
| POST | `/api/v1/auth` | Agent-key challenge and private-read token |
| POST | `/api/v1/exchange` | Separately signed durable trading action |
| GET | `/api/v1/ws` | Public/private streams and signed posts |
| GET | `/healthz` | Operational liveness |

Actions: `order`, `batch_modify`, `cancel`, `cancel_by_client_order_id`, `cancel_all`,
`schedule_cancel`, `update_leverage`, and `set_fee_mode`.

Order time in force: `Alo`, `Ioc`, or `Gtc`. There is no unbounded market-order action. A client must
read BBO, calculate an explicit protection price and submit a limit `Ioc`.

An exchange response with `response.type = accepted` means the command journal and nonce are
durable. The eventual transaction is `applied` or `rejected`. Do not infer an order status from the
transport response.

Stable API errors include `invalid_schema`, `invalid_signature`, `duplicate_nonce`,
`expired_request`, `rate_limited`, `permission_denied`, and `engine_unavailable`. Use codes, never
parse messages. When present, respect `retry_after_ms`.

The server `meta` response is the source of truth for API/schema/rulebook version, fees, markets,
FairLag duration, settlement mode and reference-source label.

