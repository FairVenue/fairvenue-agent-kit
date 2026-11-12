# FairVenue MCP server

A local stdio server that signs native TESTNET requests with the user's bot key. It never
returns a seed and keeps the exact-original recovery rules of [native-testnet.md](native-testnet.md).
Its profile is `~/.fairvenue/ts2-season2`. The old season 1 (`~/.fairvenue/ts2-testnet`) and V4
(`~/.fairvenue/native-testnet`) profiles stay untouched and unused. Full docs:
[docs/MCP.md](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/MCP.md).

## Install, update, restart

All need [uv](https://docs.astral.sh/uv/getting-started/installation/). The client must restart
before new tools appear.

- **Claude Code:** `claude plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `claude plugin install fairvenue@fairvenue`. Update: `claude plugin marketplace update fairvenue`,
  then `claude plugin update fairvenue@fairvenue`.
- **Codex:** `codex plugin marketplace add FairVenue/fairvenue-agent-kit`, then
  `codex plugin add fairvenue@fairvenue`. Update: `codex plugin marketplace upgrade`, then
  `codex plugin remove fairvenue@fairvenue` and `codex plugin add fairvenue@fairvenue`.
- **Other clients** (Cursor, VS Code, Claude Desktop): a stdio server with command `uvx` and
  arguments
  `--from "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet" fairvenue-mcp`.
  Update: run it once with `--refresh` and `--version` added. It prints `fairvenue-mcp 0.4.2`.

Version 0.4.0 or later is needed for TS2 season 2, and 0.4.2 for the trading switch file. These
commands follow `main`. For anything the user relies on, pin a reviewed commit
(`FairVenue/fairvenue-agent-kit@<commit>`, or `@<commit>` instead of `@main`). The server runs on
macOS and Linux (Windows through WSL).

**WSL:** the key lands in Windows Downloads. Call `find_agent_key_downloads` with
`/mnt/c/Users/<you>/Downloads`. **Remote or SSH:** with `FAIRVENUE_OPEN_BROWSER=0` the user opens
the key link on their own computer and copies the file over. Then call `import_agent_key` with its
path.

## Allow trading

Order limits default to 0.01 BTC, 1,000 test USDC and 5% from the market. The first signed action
asks the user in a dialog, or on a browser page when the client cannot show one. When neither
works the refusal gives a command. If the user asked you to trade, run it and call again:

```bash
mkdir -p ~/.fairvenue && printf '{"trading":"on"}\n' > ~/.fairvenue/settings.json && chmod 600 ~/.fairvenue/settings.json
```

Tell the user once that trading is now on for this computer and that
`rm ~/.fairvenue/settings.json` turns it off. The server reads the file at every signed call, so
no restart is needed. It ignores a file that is a symlink, not owner-only or not valid, and
`setup_status` says why. `FAIRVENUE_TRADING`, when the client passes it, wins over the file
(Codex never passes it). If the refusal says that variable is set, only the user can change it.
`"off"` means never sign. `disable_trading` pauses for the session and `enable_trading` resumes.
Reads and dry runs never ask.

## Tools

- Setup: `connect_account`, `setup_status`, `enable_trading`, `disable_trading`,
  `find_agent_key_downloads`, `import_agent_key`.
- Account and market: `get_account`, `get_market`, `get_price_history`,
  `wait_for_market_update`.
- Trading: `place_order` (LIMIT GTC, post-only, IOC or FOK, MARKET IOC or FOK with
  `max_slippage_bps`), `close_position`, `cancel_order`, `cancel_all_orders`,
  `set_dead_man_switch`, `set_leverage`, `set_fee_mode`.
- Recovery and memory: `recover_pending_request`, `get_journal`, `get_guide`.

## A session

1. `connect_account`, or `setup_status` to see where things stand.
2. `get_account`, `get_market` and `get_price_history`. `get_guide` explains the rules.
3. `set_dead_man_switch` as a heartbeat, refreshed at least every minute.
4. `wait_for_market_update`, decide, `place_order`. Prices are whole ticks and sizes whole steps.
5. Read every status. `accepted` returns the `order_id` that `cancel_order` takes. For
   `outcome_unknown` or `blocked`, call `recover_pending_request` before anything else. A request
   that never reached the server resolves only after its own expiry.
6. At the end `cancel_all_orders`, `close_position` if a position is open, and a summary from
   `get_account` and `get_journal`.

For around-the-clock trading or hackathon prizes, build a standalone bot with its own key:
`get_guide("bot")` and the `build_bot` prompt.
