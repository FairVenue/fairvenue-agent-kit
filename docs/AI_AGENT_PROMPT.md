# Copy-paste coding-agent prompt

Replace the two angle-bracket sections. Never paste credentials into chat.

```text
Integrate this repository with the FairVenue Arena virtual-settlement testnet.

Strategy: <describe the strategy in one or two sentences>
Runtime: <for example Python 3.12 on Linux>

Use the `fairvenue` Python package from https://github.com/FairVenue/fairvenue-agent-kit.
Read its README.md, docs/API_V1.md, docs/SIGNING.md and docs/WEBSOCKET.md before changing code.
Pin a reviewed commit. Do not invent endpoints, fields, enum values or a hosted testnet URL.

Read FAIRVENUE_API_URL and FAIRVENUE_CREDENTIALS from the environment. Never print, commit or ask
me to paste the Ed25519 seed, bearer token, complete signed envelope or private stream events.

If I need a bot key, give me https://app.fairvenue.xyz/?setup=api-key (or the operator-provided
Arena origin with /?setup=api-key) and ask me to click Create & download API key, then say "done".
Help locate and secure the downloaded file without exposing its contents to chat or model context.
Handle available local setup yourself and guide me through the first no-order dry run.

Requirements:
- Arena is a simulator. Never send user orders or credentials to Binance or another venue.
- External BTCUSDC data is reference context only.
- Keep money as decimal strings or Decimal, never float.
- Use Alo for post-only orders.
- For taker behavior, read BBO and submit a price-protected Ioc. There is no market-order action.
- Give each retriable logical order a stable client_order_id.
- Treat accepted as durable receipt only; confirm applied/rejected through transaction_status or a
  private WebSocket event before changing strategy state.
- Use the SDK signing and nonce implementation; do not hand-roll either.
- Reconnect with bounded exponential backoff, reauthenticate, resubscribe and discard the local book
  after any sequence gap.
- Respect retry_after_ms and keep cancel/dead-man-switch traffic available under order throttling.
- Arm and refresh schedule_cancel while active; cancel all during graceful shutdown.
- Validate meta.api_version, environment, settlement and market configuration at startup.

Deliver an isolated FairVenue adapter, typed validated config, a no-order dry-run mode, a runnable
testnet command, tests for decimal/slippage/reconnect/accepted-vs-applied behavior, an .env.example
without secrets, and concise setup/shutdown documentation. Run formatter, linter, type checker and
tests, then report exact commands and unresolved API assumptions.
```
