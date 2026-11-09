# FairVenue MCP server

The `fairvenue` MCP server runs locally over stdio and signs native TESTNET requests with the
user's bot key. It never returns a seed, asks the user in a dialog before it signs anything and
follows the exact-original recovery rules in [native-testnet.md](native-testnet.md). The user
only clicks: once in the browser to create a key and once in the dialog to allow trading.

Since version 0.3.0 the server speaks the TS2 testnet. The tool names and arguments are the same
as before. Bot keys made before TS2 are refused with "TS2: create a new bot key", so the user
creates a new key once. The server keeps its TS2 profile in `~/.fairvenue/ts2-testnet` and never
touches the old `~/.fairvenue/native-testnet` profile.

## Install it

All options need [uv](https://docs.astral.sh/uv/getting-started/installation/). Ask before
installing anything on the user's machine.

- **Claude Code** (skill and server together):
  `/plugin install fairvenue --marketplace FairVenue/fairvenue-agent-kit`.
  On versions before 2.1.275: `/plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `/plugin install fairvenue@fairvenue`.
- **Codex**: `codex plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `codex plugin add fairvenue@fairvenue`.
- **Any other MCP client** (Cursor, VS Code, Claude Desktop): a stdio server with command `uvx`
  and arguments
  `--from "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet" fairvenue-mcp`.

These commands follow the default branch. For anything the user relies on, pin the reviewed
commit: `FairVenue/fairvenue-agent-kit@<commit>` in the marketplace commands and
`fairvenue-agent-kit@<commit>` instead of `@main` in the `uvx` URL. The server runs on macOS and
Linux (Windows through WSL). The client must be restarted or reloaded before new tools appear.
Full instructions:
[docs/MCP.md](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/MCP.md).

## Connect and trade with clicks only

`connect_account` does the whole setup. It opens the key page in the user's browser, waits while
they sign in with Google and click **Create API key & download JSON**, picks the downloaded key
up by itself, stores it privately and runs a signed no-order check. Pass its `say_to_user` text
on to the user and call it again while the status is `waiting_for_key`. Never ask the user to
type done, paste a key or move a file.

The first signed action of a session shows the user one dialog to allow trading, with the
per-order limits filled in (0.01 BTC, 1,000 test USDC and 5% from the market by default). The
dialog's answer comes from the user, never from you. If the client cannot show MCP dialogs, the
user sets `FAIRVENUE_TRADING=on` in the server environment instead. `disable_trading` stops
signing for the rest of the session.

## Typical session

1. `connect_account` (or `setup_status` to see where things stand).
2. `get_market` and `get_price_history` to form a view. `get_guide` explains the rules.
3. `set_dead_man_switch` as a heartbeat while trading, refreshed at least every minute.
4. `wait_for_market_update`, decide, `place_order` (with `dry_run` first for a new order shape).
   Prices must be whole ticks and sizes whole steps from `get_market`. Nothing is rounded.
5. Read every status. `accepted` means the exchange journaled it and returns the order's
   `order_id`, which `cancel_order` takes. `rejected` means it was refused. `not_placed`,
   `not_executed` and `failed` mean nothing happened. `blocked` and `outcome_unknown` mean call
   `recover_pending_request` before anything else, even before switching to a new key.
6. There is no order modify. `modify_order` refuses, so cancel and place a new order.
7. `cancel_all_orders` (it cancels every working order at once) and a summary from
   `get_journal` at the end.

For around-the-clock trading, build a standalone bot with its own key instead of looping in
chat. The `build_bot` prompt and `get_guide("bot")` describe it.
