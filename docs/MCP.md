# FairVenue MCP server

`fairvenue-mcp` gives a coding agent hands on the FairVenue native TESTNET. The `fairvenue` skill
teaches the agent how the testnet works and the MCP server lets it act: connect your account,
read your balances and position, read the market, sign orders within your limits and recover
requests whose outcome is unknown. You only click: once in the browser to create a key, and once
in a dialog (or a browser page, when your client cannot show the dialog) to allow trading.

It runs locally over stdio on macOS and Linux (Windows through WSL). It is built on the native SDK
in [`experimental/native-testnet`](../experimental/native-testnet/README.md), so it keeps the same
exact-original recovery rules, and it only talks to the approved HTTPS origin from the installed
pins. This is an operator-run Arbitrum Sepolia TESTNET with mock USDC. It handles no real funds,
does not connect to mainnet and never routes an order to another venue.

## What's new in 0.4.1

**Browser confirmation when the client cannot show the dialog.** Some clients advertise MCP
dialogs but answer them without showing anything. Codex did, so `enable_trading` and the first
order or `close_position` failed with "The user did not allow trading" although nobody was asked.
The trading dialog stays the first choice. When the client has no dialogs, or a decline or cancel
comes back in under 1.5 seconds (faster than anyone can read the question), the server opens a
one-shot confirmation page in your browser with the same question and limits. Click **Allow**
and the original call goes on, for example the position closes. Click **Deny** and nothing is
signed. A decline that takes longer stays your answer. See
[Allowing trading](#allowing-trading).

## What's new in 0.4.0

Version 0.4.0 speaks TS2 season 2 and turns the server into a full trading tool. Earlier
versions cannot connect to season 2.

- **`get_account`:** balances, available and locked margin, realized and unrealized PnL, the
  position, fee mode and leverage, open and FairLag-held orders and recent fills with their
  settlement state. It is a signed read: no order, no dialog.
- **MARKET orders and FOK.** `place_order` takes `order_type` LIMIT or MARKET and `time_in_force`
  GTC, IOC or FOK. A MARKET order signs a worst price `max_slippage_bps` beyond the best opposite
  price (default 50, the Arena terminal's 0.5%).
- **`close_position`:** a reduce-only MARKET order for the whole position or part of it.
- **Paused trading is explained.** While admission is closed nothing is signed except cancels,
  and the result says why in plain words.
- **One signed context read per order** instead of two.
- **Removed:** `modify_order` (TS2 has no modify), `install_deployment_pins` and
  `verify_connection` (`connect_account` does both), and the unused `within_seconds` argument of
  `cancel_all_orders`.

### Update and create a new key

Season 2 is a fresh deployment, so everyone updates once and creates one new key.

1. **Update the server.**
   - Claude Code: run `claude plugin marketplace update fairvenue`, then
     `claude plugin update fairvenue@fairvenue`, and restart Claude Code.
   - Codex: run `codex plugin marketplace upgrade`, then `codex plugin remove fairvenue@fairvenue`
     and `codex plugin add fairvenue@fairvenue`, and restart Codex.
   - Cursor, VS Code, Claude Desktop and other `uvx` setups: run the server command once with
     `--refresh`, for example `uvx --refresh --from "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet" fairvenue-mcp --version`.
     It prints `fairvenue-mcp 0.4.1`. Then restart the client.
2. **Create a new key.** Ask your agent *"Connect me to the FairVenue TESTNET"*.
   `connect_account` opens the key page. Sign in with Google and click
   **Create API key & download JSON**. Your account gets 1,000 test USDC once in season 2, right
   after sign-in. Keys from season 1 or the older V4 testnet are refused.

The server keeps its files in `~/.fairvenue/ts2-season2`. The season 1 profile
`~/.fairvenue/ts2-testnet` and the V4 profile `~/.fairvenue/native-testnet` are left untouched
and are not used.

## Who should install it

- **Claude Code and Codex:** recommended. One plugin command installs the skill and the server,
  and the skill offers it by default (it asks before installing anything).
- **Cursor, VS Code, Claude Desktop:** optional. It needs `uv` and a short config entry. Clients
  that cannot show the trading dialog get a confirmation page in the browser instead.
- **Prize-seeking hackathon bots:** use the server to explore, test and build, then run a
  standalone bot with the native SDK and its own key. Prizes need a full UTC day of hourly
  returns, which a chat session rarely covers.

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv` or
`curl -LsSf https://astral.sh/uv/install.sh | sh`). `uvx` builds the server in an isolated
environment on first start.

### Claude Code: skill and MCP server together (recommended)

```text
/plugin install fairvenue --marketplace FairVenue/fairvenue-agent-kit
```

On Claude Code before 2.1.275 use two steps:

```text
/plugin marketplace add FairVenue/fairvenue-agent-kit
/plugin install fairvenue@fairvenue
```

The plugin bundles the `fairvenue` skill and this server, and runs the server from the same
checkout as the skill. There is nothing to configure.

### Codex

The repository is also a portable [Agent Plugin](https://agent-plugins.org) (`plugin.json`,
`mcp.json` and `skills/`) with a marketplace file:

```bash
codex plugin marketplace add FairVenue/fairvenue-agent-kit
codex plugin add fairvenue@fairvenue
```

Codex then runs the server from the installed plugin copy and loads the skill. Or add only the
server:

```bash
codex mcp add fairvenue -- uvx --from "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet" fairvenue-mcp
```

The same entry in `~/.codex/config.toml`:

```toml
[mcp_servers.fairvenue]
command = "uvx"
args = ["--from", "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet", "fairvenue-mcp"]
```

If your Codex version can't show MCP confirmation dialogs, the server asks on a confirmation page
in your browser instead (version 0.4.1 and later). Without a desktop browser, allow trading up
front with `[mcp_servers.fairvenue.env]` and `FAIRVENUE_TRADING = "on"`.

### Cursor, VS Code, Claude Desktop and other MCP clients

Every client runs the same command:

```json
{
  "mcpServers": {
    "fairvenue": {
      "command": "uvx",
      "args": [
        "--from",
        "fairvenue-schema6-agent[mcp] @ git+https://github.com/FairVenue/fairvenue-agent-kit@main#subdirectory=experimental/native-testnet",
        "fairvenue-mcp"
      ]
    }
  }
}
```

- **Cursor:** put that in `~/.cursor/mcp.json`, or paste this install link into your browser:
  `cursor://anysphere.cursor-deeplink/mcp/install?name=fairvenue&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJmYWlydmVudWUtc2NoZW1hNi1hZ2VudFttY3BdIEAgZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS9GYWlyVmVudWUvZmFpcnZlbnVlLWFnZW50LWtpdEBtYWluI3N1YmRpcmVjdG9yeT1leHBlcmltZW50YWwvbmF0aXZlLXRlc3RuZXQiLCJmYWlydmVudWUtbWNwIl19`
- **VS Code:** the file is `.vscode/mcp.json` and its top-level key is `servers` instead of
  `mcpServers`, with `"type": "stdio"` on the entry.
- **Claude Desktop:** add it to `claude_desktop_config.json`. If the app can't find `uvx`, use
  the absolute path printed by `which uvx`.

Add the skill too, so the agent knows the workflow and rules:

```bash
npx skills add FairVenue/fairvenue-agent-kit --skill fairvenue
```

### WSL and remote machines

- **WSL:** the Windows browser saves to Windows. Add
  `"env": {"FAIRVENUE_DOWNLOAD_DIRS": "/mnt/c/Users/<you>/Downloads"}` to the server entry so
  `connect_account` finds the key there.
- **Remote or SSH machines** (dev containers, Codespaces, a server over SSH): add
  `"env": {"FAIRVENUE_OPEN_BROWSER": "0"}`. `connect_account` then only returns the key page
  link. Open it on your own computer, sign in and download the key. Copy the file to the remote
  machine into a private folder, for example
  `scp fairvenue-*.json remote:~/fairvenue-key/`. Then ask the agent to import it with
  `import_agent_key` and that path, or to search the folder with `find_agent_key_downloads`.
  The server copies it into its profile with mode 0600. Then delete the copies you made.
- With the Claude Code or Codex plugin there is no `env` entry to edit. Export the variable in
  the shell that starts the client instead, for example
  `export FAIRVENUE_DOWNLOAD_DIRS=/mnt/c/Users/<you>/Downloads`.

### Pin a reviewed commit

`@main` and the marketplace commands above follow the default branch, which is fine for trying
the kit. For anything you rely on, pin the reviewed commit, as the native SDK guide requires:
`/plugin marketplace add FairVenue/fairvenue-agent-kit@<commit>` in Claude Code,
`codex plugin marketplace add FairVenue/fairvenue-agent-kit@<commit>` in Codex, and
`git+https://github.com/FairVenue/fairvenue-agent-kit@<commit>#subdirectory=experimental/native-testnet`
in a `uvx` command.

## First run

Ask your agent *"Connect me to the FairVenue TESTNET"*, or use the `onboard` prompt. The agent
calls `connect_account`, which does the rest:

1. It installs the reviewed deployment pins after checking their SHA256.
2. It opens [the key page](https://app.fairvenue.xyz/?setup=api-key#native-agent-key) in your
   browser and gives the agent the same link, in case the browser can't be opened.
3. It waits while you sign in with Google (if asked) and click
   **Create API key & download JSON**. The button turns on as soon as your account has its
   1,000 test USDC, usually right after sign-in. The matching mock-token deposit on Arbitrum
   Sepolia follows in the background. Trading never waits for it.
4. It notices the download within a second, moves the key into its private profile (mode 0600)
   and deletes the copy in Downloads. Nobody sees the key's contents.
5. It runs a signed check that submits no order and reports your public owner address.

### Allowing trading

You don't type anything in between. The first time the agent signs an order or an account
setting, your client shows one dialog: **Allow trading for this session**, with the per-order
limits filled in. Click accept and the agent trades within those limits until the session ends,
or until it calls `disable_trading`.

When the client cannot show the dialog, the server asks in your browser instead:

- It falls back when the client has no MCP dialogs, or when a decline or cancel comes back in
  under 1.5 seconds. Nobody reads the question that fast. Codex answered it in about 5 ms
  without showing anything. A decline or cancel that takes longer is your answer, and nothing
  is signed.
- The page opens at `http://127.0.0.1` on a random port, under a random secret path. It shows
  the same question and limits as the dialog, with **Allow** and **Deny**. Allow uses the
  default limits (the `FAIRVENUE_MAX_*` settings). Only the page itself can answer. Requests
  from other sites or other host names are refused.
- The tool call waits up to 45 seconds for your click. After Allow the original call goes on,
  so the order is placed or the position closes. After Deny nothing is signed.
- If you don't click in time, the agent asks you to approve in the browser page and then to ask
  again. The page keeps listening for 10 minutes and the next call picks up your click. A call
  that finds it still unanswered shows it again.
- The secret page address goes only to your browser. It is never shown to the agent, logged or
  journaled, so the agent cannot approve trading by itself. That is why there is no link with
  `FAIRVENUE_OPEN_BROWSER=0` or without a desktop browser. A remote machine's `127.0.0.1` page
  would not open on your own computer anyway. Allow trading up front there with
  `FAIRVENUE_TRADING=on`.

## Tools

| Tool | What it does |
| --- | --- |
| `connect_account` | The whole setup in one call: pins, key page in the browser, key pickup, signed no-order check. |
| `setup_status` | Local readiness, pending requests, trading switch, limits and the next step. No network. |
| `enable_trading` | Asks the user now, in the one-click dialog or the browser confirmation page, to allow trading for the session. |
| `disable_trading` | Stops signing until the user allows trading again. |
| `find_agent_key_downloads` | Lists downloaded TS2 keys with public fields only, for keys saved elsewhere. |
| `import_agent_key` | Copies a key file into the profile (0600) and makes it active. Refuses V4 and season 1 keys. |
| `get_account` | Signed read: balances, margin, PnL, position, fee mode, leverage, open orders, recent fills and their settlement. |
| `get_market` | Book, mark, midpoint, spread, recent trades with settlement state, fees, tick, step and admission. |
| `get_price_history` | Native book midpoints or oracle marks with low, high and change. |
| `wait_for_market_update` | Blocks until the market changes, the event loop for trading agents. |
| `place_order` | One LIMIT (GTC, post-only GTC, IOC, FOK) or MARKET (IOC, FOK, slippage-bounded) order. Returns its `order_id`. `dry_run` previews it. |
| `close_position` | Closes all or part of the position with a reduce-only MARKET IOC. `dry_run` previews it. |
| `cancel_order` | Cancels one order by the `order_id` from `place_order` or `get_account`. |
| `cancel_all_orders` | Cancels every resting and FairLag-held order of the account at once. |
| `set_dead_man_switch` | Heartbeat that cancels all working orders right after its deadline unless refreshed. |
| `set_leverage` | Account leverage 1 to 100. It can fall only with no position and no working orders. |
| `set_fee_mode` | FIXED or PNL. It changes only with no position and no working orders. |
| `recover_pending_request` | Looks up an unknown outcome by its exact original. Never resends. |
| `get_journal` | This server's own requests, outcomes and order IDs. |
| `get_guide` | Overview, setup, trading, recovery, hackathon rules and bot building. |

Prompts: `onboard`, `trading_session`, `build_bot` and `hackathon_plan`. The guides are also
resources at `fairvenue://guide/overview`, `setup`, `trading`, `recovery`, `hackathon` and `bot`.

### Orders

- **LIMIT** needs a `price` on the tick. `time_in_force` GTC rests (23 hours by default,
  `FAIRVENUE_GTC_LIFETIME_MINUTES`), IOC fills what it can now, FOK fills completely or not at
  all. `post_only` needs GTC.
- **MARKET** takes no price. It is IOC (default) or FOK and signs a worst price
  `max_slippage_bps` beyond the best opposite price, or beyond the mark when that side is empty.
  The default is 50 bps. It may not exceed `FAIRVENUE_MAX_PRICE_DEVIATION_BPS`.
- **`close_position`** reads the position and signs a reduce-only MARKET IOC on the other side.
  The size and notional limits do not apply, because it can only shrink the position.
- Sizes are whole steps and prices whole ticks. Nothing is rounded.
- **Paused trading:** while admission is closed nothing is signed except cancels and
  `cancel_all_orders` (the exchange decides those), and reduce-only orders while the mark catches
  up. The result is `not_placed` with reason `admission_closed`, the reason codes and the
  explanation in plain words. During a funding payment only cancels work.

### What a signed request can return

| Status | Meaning |
| --- | --- |
| `accepted` | The exchange journaled it. An order then waits for FairLag. Orders carry their `order_id`. |
| `rejected` | The exchange refused it. Nothing executed. |
| `not_placed` | Nothing was placed, with a `reason`: `rejected`, `invalid_request`, `admission_closed` (with the reason in plain words) or `rate_limited`. A new request is safe. A refused leverage or fee-mode change explains the position and orders rule. |
| `not_executed` | Its batch was voided. Nothing happened. |
| `failed` | It failed before admission (network, timeout, an off-tick price, context change). Nothing happened. |
| `blocked` | A request of this or another key has an unknown outcome. Nothing was signed. |
| `outcome_unknown` | It may have executed. Call `recover_pending_request` before anything else. |

`recover_pending_request` answers `accepted`, `rejected`, `not_placed` (it never reached the
journal and its expiry passed) or `not_settled` when it resolves the request, `unresolved` or
`still_unknown` when it should be asked again later, and `nothing_pending` when nothing is
waiting. A result with `journal_error` is still true: only its local journal entry is missing.

**Know this about unknown outcomes.** If an order was stored but never reached the server (for
example the network dropped before sending), its lookup stays `unresolved` until that order's
own expiry has passed. Signing waits until then: 30 seconds for IOC, FOK, MARKET and account
commands, the GTC lifetime for a resting order. Keep calling `recover_pending_request`. A
shorter `FAIRVENUE_GTC_LIFETIME_MINUTES` shortens that wait for GTC orders.

Read tools carry `readOnlyHint` (`get_account` too: it changes nothing). Signing tools carry
`destructiveHint`, so clients can ask before each signature while letting the agent read freely.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `FAIRVENUE_TRADING` | `ask` | `ask`: one-click dialog (or the browser confirmation page) before the first signed action of a session. `on`: no question. `off`: never sign. |
| `FAIRVENUE_MAX_ORDER_QUANTITY` | `0.01` | Default largest order in BTC. The dialog can change it for the session. |
| `FAIRVENUE_MAX_ORDER_NOTIONAL` | `1000` | Default largest price times size in test USDC. |
| `FAIRVENUE_MAX_PRICE_DEVIATION_BPS` | `500` | Refuse prices, and MARKET worst prices, further than this from the midpoint (or the mark when one side is empty). It also caps `max_slippage_bps`. |
| `FAIRVENUE_GTC_LIFETIME_MINUTES` | `1380` (23 hours) | How long a GTC order rests unless cancelled or filled. 1 to 1380. |
| `FAIRVENUE_HOME` | `~/.fairvenue/ts2-season2` | Private profile directory (0700). Never point it at an old V4 or season 1 profile. |
| `FAIRVENUE_KEY_FILE` | active imported key | Always use this TS2 key file. It overrides `import_agent_key`. |
| `FAIRVENUE_INSTALLATION_FILE` | `$FAIRVENUE_HOME/installation.json` | Installed deployment pins. |
| `FAIRVENUE_INSTALLATION_SHA256` | packaged pins | SHA256 of a separately reviewed pin file for the same TS2 deployment, for example another approved API origin. A new deployment needs an updated kit. |
| `FAIRVENUE_DOWNLOAD_DIRS` | `~/Downloads` | Where `connect_account` watches for the key. Separate several with `:`. |
| `FAIRVENUE_OPEN_BROWSER` | `on` | `0` or `off` makes `connect_account` only return the link, for remote or headless machines. It also turns off the browser confirmation page, so clients without the dialog need `FAIRVENUE_TRADING=on`. |

`fairvenue-mcp --check` prints the local setup status as JSON without starting the server.

## Safety model

- **Keys stay local.** The server reads key files and signs in its own process. No tool returns,
  logs or journals a seed, signature or signed envelope. Agents never need to open a key file.
- **Deployment pins come from this repository**, verified by SHA256, never from a key file or an
  API reply. `get_market` warns when the served config hash differs from the installed pins.
- **Old keys never become season 2 authority.** A V4 key file is refused with
  "TS2: create a new bot key" and a key of another deployment, such as a TS2 season 1 key, is
  refused too. Neither is ever converted.
- **You decide in a dialog.** No order or account setting is signed until you click accept in
  the trading dialog, or Allow on the browser confirmation page (or set `FAIRVENUE_TRADING=on`).
  Signed reads (the connection check, `get_account`) and recovery lookups need no dialog. The
  answer lasts for the server session only. The model cannot answer the dialog for you, and it
  never sees the confirmation page's secret address.
- **The browser is opened, never driven.** `connect_account` only opens the key page, and the
  trading confirmation only its own `127.0.0.1` page, with your system's default browser. You
  sign in and click yourself.
- **Local guards** refuse orders above the size or notional limits, prices or MARKET worst prices
  far from the reference, prices off the tick and sizes off the step (never rounded), post-only
  orders that would cross, and anything but cancels while trading is paused.
- **Exact-original recovery.** Every signed request is retained before admission. A timeout leaves
  it pending, and nothing new is signed with any key until `recover_pending_request` resolves it
  from the request's own batch history. Switching keys is refused meanwhile, and an expired key
  can still be imported to recover its own request. Nothing is ever resent, repriced or resigned.
- **Journal before signing.** A public summary of each request is journaled before it is signed.
  If the journal cannot be written, nothing is signed. A later journal failure never hides the
  real outcome.
- **One key, one process.** The server holds the key's continuity lock. A separate bot program,
  or a second MCP session, needs its own key. `setup_status` reports when another process holds
  the lock.

## Files

```text
~/.fairvenue/ts2-season2/             0700
  installation.json                   reviewed TS2 deployment pins, 0600
  keys/<agent>-<delegation>.json      imported TS2 bot keys, 0600
  continuity/<agent>-<delegation>/    exact-original custody for each key. Never delete it or the key.
  state.json                          active key and initialized namespaces
  journal.jsonl                       public summaries of requests and outcomes
```

## Limits to know

- `get_account` and `accepted` are soft, operator-trusted state. They are not an SP1 proof or L1
  finality. Each fill shows its settlement state.
- Season 2 proves fills only. A cancel, `cancel_all_orders` or dead man's switch is enforced by
  the operator's matching engine, not by the proof.
- `get_account` shows the latest 64 orders and fills. FairLag-held orders are counted, never
  shown.
- Public reads and signed calls share global rate limits with every participant: about 600
  signed reads a minute, 120 commands a minute in total, and 60 orders and account settings a
  minute per account. Public market reads are served from a short shared cache. Each order costs
  three signed calls (one context read, the admission and the exact lookup) and `get_account`
  two signed reads. Let the agent wait for market updates instead of polling.
- Keys expire. `connect_account` notices and opens the key page again.
