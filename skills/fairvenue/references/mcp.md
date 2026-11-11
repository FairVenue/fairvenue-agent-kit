# FairVenue MCP server

The `fairvenue` MCP server runs locally over stdio and signs native TESTNET requests with the
user's bot key. It never returns a seed, asks the user in a dialog before it signs an order or
an account setting and follows the exact-original recovery rules in
[native-testnet.md](native-testnet.md). The user only clicks: once in the browser to create a
key and once in the dialog to allow trading.

Version 0.4.0 speaks TS2 season 2. Earlier versions cannot connect, so if `setup_status` shows a
version below 0.4.0, ask the user to update (the commands are below) and restart the client. Bot
keys from season 1 or the V4 testnet are refused, so the user creates a new key once. The server
keeps its season 2 profile in `~/.fairvenue/ts2-season2` and never touches the old
`~/.fairvenue/ts2-testnet` (season 1) or `~/.fairvenue/native-testnet` (V4) profiles. Season 2
proves fills only. Cancels, cancel-all and the dead man's switch are enforced by the operator's
matching engine, not by the proof.

## When to offer it

- **Claude Code and Codex:** offer it by default, once per conversation, before other native
  setup. Ask before installing anything.
- **Cursor, VS Code, Claude Desktop and other clients:** mention it as an option. It needs `uv`
  and a config entry, and some clients cannot show the trading dialog.
- **Prize-seeking hackathon participants:** the server is for exploring, testing and building.
  Prizes need a full UTC day of hourly returns, so steer them to a standalone bot with the
  native SDK and its own key.

## Install or update it

All options need [uv](https://docs.astral.sh/uv/getting-started/installation/). Ask before
installing anything on the user's machine.

- **Claude Code** (skill and server together):
  `/plugin install fairvenue --marketplace FairVenue/fairvenue-agent-kit`.
  On versions before 2.1.275: `/plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `/plugin install fairvenue@fairvenue`. To update: `claude plugin marketplace update fairvenue`,
  then `claude plugin update fairvenue@fairvenue`, then restart Claude Code.
- **Codex**: `codex plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `codex plugin add fairvenue@fairvenue`. To update: `codex plugin marketplace upgrade`, then
  `codex plugin remove fairvenue@fairvenue` and `codex plugin add fairvenue@fairvenue`, then
  restart Codex.
- **Any other MCP client** (Cursor, VS Code, Claude Desktop): a stdio server with command `uvx`
  and arguments
  `--from "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet" fairvenue-mcp`.
  To update, run that command once with `--refresh` and `--version` added (it prints
  `fairvenue-mcp 0.4.0`), then restart the client.

These commands follow the default branch. For anything the user relies on, pin the reviewed
commit: `FairVenue/fairvenue-agent-kit@<commit>` in the marketplace commands and
`fairvenue-agent-kit@<commit>` instead of `@main` in the `uvx` URL. The server runs on macOS and
Linux (Windows through WSL). The client must be restarted or reloaded before new tools appear.
Full instructions:
[docs/MCP.md](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/MCP.md).

**WSL:** set `FAIRVENUE_DOWNLOAD_DIRS=/mnt/c/Users/<you>/Downloads` in the server environment.
**Remote or SSH:** set `FAIRVENUE_OPEN_BROWSER=0`. The user opens the link on their own computer,
downloads the key and copies the file to the remote machine (for example with `scp` into a
private folder). Then call `import_agent_key` with that path. This is the one case where the
user moves a file.

## Connect and trade with clicks only

`connect_account` does the whole setup. It opens the key page in the user's browser, waits while
they sign in with Google and click **Create API key & download JSON**, picks the downloaded key
up by itself, stores it privately and runs a signed no-order check. Pass its `say_to_user` text
on to the user and call it again while the status is `waiting_for_key`. Never ask the user to
type done or paste a key.

The first signed order or account setting of a session shows the user one dialog to allow
trading, with the per-order limits filled in (0.01 BTC, 1,000 test USDC and 5% from the market
by default). The dialog's answer comes from the user, never from you. If the client cannot show
MCP dialogs, the user sets `FAIRVENUE_TRADING=on` in the server environment instead.
`disable_trading` stops signing for the rest of the session. `get_account` and dry runs never
ask.

## Tools

- Setup: `connect_account`, `setup_status`, `enable_trading`, `disable_trading`,
  `find_agent_key_downloads`, `import_agent_key`.
- Account and market: `get_account`, `get_market`, `get_price_history`,
  `wait_for_market_update`.
- Trading: `place_order` (LIMIT GTC, post-only, IOC or FOK, MARKET IOC or FOK with
  `max_slippage_bps`), `close_position`, `cancel_order`, `cancel_all_orders`,
  `set_dead_man_switch`, `set_leverage`, `set_fee_mode`.
- Recovery and memory: `recover_pending_request`, `get_journal`, `get_guide`.

## Typical session

1. `connect_account` (or `setup_status` to see where things stand).
2. `get_account` for balances, the position and open orders. `get_market` and
   `get_price_history` to form a view. `get_guide` explains the rules.
3. `set_dead_man_switch` as a heartbeat while trading, refreshed at least every minute.
4. `wait_for_market_update`, decide, `place_order` (with `dry_run` first for a new order shape).
   Prices must be whole ticks and sizes whole steps from `get_market`. Nothing is rounded. A
   MARKET order takes no price: keep `max_slippage_bps` small.
5. Read every status. `accepted` means the exchange journaled it and returns the order's
   `order_id`, which `cancel_order` takes. `rejected` means it was refused. `not_placed`,
   `not_executed` and `failed` mean nothing happened. `not_placed` with reason
   `admission_closed` means trading is paused: relay the plain-word explanation. `blocked` and
   `outcome_unknown` mean call `recover_pending_request` before anything else, even before
   switching to a new key. If a request never reached the server, its lookup resolves only once
   its own expiry has passed.
6. There is no order modify: cancel and place a new order.
7. At the end `cancel_all_orders`, `close_position` if a position is open, and a summary from
   `get_account` and `get_journal`.

For around-the-clock trading, build a standalone bot with its own key instead of looping in
chat. The `build_bot` prompt and `get_guide("bot")` describe it.
