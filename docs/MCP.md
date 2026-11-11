# FairVenue MCP server

`fairvenue-mcp` gives a coding agent hands on the FairVenue native TESTNET. The `fairvenue` skill
teaches the agent how the testnet works and the MCP server lets it act: connect your account,
read the market, sign orders within your limits and recover requests whose outcome is unknown.
You only click: once in the browser to create a key, and once in a dialog to allow trading.

It runs locally over stdio on macOS and Linux (Windows through WSL). It is built on the native SDK
in [`experimental/native-testnet`](../experimental/native-testnet/README.md), so it keeps the same
exact-original recovery rules, and it only talks to the approved HTTPS origin from the installed
pins. This is an operator-run Arbitrum Sepolia TESTNET with mock USDC. It handles no real funds,
does not connect to mainnet and never routes an order to another venue.

## Upgrading to TS2 season 2 (version 0.3.1)

The testnet now runs TS2 season 2, a fresh deployment with new pins, and version 0.3.1 of the
server speaks it. Version 0.3.0 still points at season 1 and can no longer connect. The tool
names and arguments are unchanged. What changes for you:

- **Create a new bot key.** Keys from season 1 or the older V4 testnet cannot sign on season 2.
  The server refuses them. `connect_account` opens the key page so you can make a new one. Your
  account gets 1,000 test USDC once in season 2, right after sign-in.
- **A fresh profile.** The server now keeps its files in `~/.fairvenue/ts2-season2`. The season 1
  profile `~/.fairvenue/ts2-testnet` and the V4 profile `~/.fairvenue/native-testnet` are left
  untouched and are not used.
- **Proofs cover fills only.** Every executed fill is proven. Cancels, `cancel_all_orders` and the
  dead man's switch are enforced by the operator's matching engine, not by the proof.
- **Restart your client** after updating so it starts the new server version.

Since 0.3.0 (the first TS2 version), orders must be whole ticks and whole steps (`tick_size` and
`step_size` in `get_market`). The server refuses other values and names the nearest valid ones.
It never rounds. An accepted order returns its `order_id`, which `cancel_order` takes. There is
no order modify: `modify_order` refuses, so cancel and place a new order.

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

If your Codex version can't show MCP confirmation dialogs, allow trading up front with
`[mcp_servers.fairvenue.env]` and `FAIRVENUE_TRADING = "on"`.

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

You don't type anything in between. The first time the agent signs an order, your client shows
one dialog: **Allow trading for this session**, with the per-order limits filled in. Click
accept and the agent trades within those limits until the session ends, or until it calls
`disable_trading`. Clients that can't show MCP dialogs need `FAIRVENUE_TRADING=on` instead.

## Tools

| Tool | What it does |
| --- | --- |
| `connect_account` | The whole setup in one call: pins, key page in the browser, key pickup, signed check. |
| `setup_status` | Local readiness, pending requests, trading switch, limits and the next step. No network. |
| `enable_trading`, `disable_trading` | Ask now in the one-click dialog, or stop signing for the session. |
| `install_deployment_pins` | Installs the reviewed packaged pins. Never overwrites a file. |
| `find_agent_key_downloads` | Lists downloaded TS2 keys with public fields only, for keys saved elsewhere. |
| `import_agent_key` | Copies a key into the profile (0600) and makes it active. Refuses V4 and season 1 keys. |
| `verify_connection` | Signed no-order context read: owner, fee mode, leverage, batch and admission. |
| `get_market` | Book, mark, midpoint, spread, recent trades with settlement state, fees, tick and step. |
| `get_price_history` | Native book midpoints or oracle marks with low, high and change. |
| `wait_for_market_update` | Blocks until the market changes, the event loop for trading agents. |
| `place_order` | One LIMIT order: GTC, post-only GTC or price-protected IOC. Returns its `order_id`. `dry_run` previews it. |
| `cancel_order` | Cancels one order by the `order_id` that `place_order` returned or the Arena terminal shows. |
| `modify_order` | Not available on TS2. It refuses and signs nothing. Cancel and place a new order. |
| `cancel_all_orders` | Cancels every resting and FairLag-held order of the account at once. |
| `set_dead_man_switch` | Heartbeat that cancels all working orders right after its deadline unless refreshed. |
| `set_leverage`, `set_fee_mode` | Account settings (leverage 1 to 100). The testnet can keep them closed. |
| `recover_pending_request` | Looks up an unknown outcome by its exact original. Never resends. |
| `get_journal` | This server's own requests, outcomes and order IDs. |
| `get_guide` | Overview, setup, trading, recovery, hackathon rules and bot building. |

Prompts: `onboard`, `trading_session`, `build_bot` and `hackathon_plan`. The guides are also
resources at `fairvenue://guide/<topic>`.

### What a signed request can return

| Status | Meaning |
| --- | --- |
| `accepted` | The exchange journaled it. An order then waits for FairLag. Orders carry their `order_id`. |
| `rejected` | The exchange refused it. Nothing executed. |
| `not_placed` | The server refused it before any effect, with a `reason`: `rejected`, `invalid_request`, `admission_closed` or `rate_limited`. A new request is safe. |
| `not_executed` | Its batch was voided. Nothing happened. |
| `failed` | It failed before admission (network, timeout, an off-tick price, context change). Nothing happened. |
| `blocked` | A request of this or another key has an unknown outcome. Nothing was signed. |
| `outcome_unknown` | It may have executed. Call `recover_pending_request` before anything else. |

`recover_pending_request` answers `accepted`, `rejected`, `not_placed` (it never reached the
journal and its expiry passed) or `not_settled` when it resolves the request, `unresolved` or
`still_unknown` when it should be asked again later, and
`nothing_pending` when nothing is waiting. A result with `journal_error` is still true: only its
local journal entry is missing.

Read tools carry `readOnlyHint`. Signing tools carry `destructiveHint`, so clients can ask before
each signature while letting the agent read freely.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `FAIRVENUE_TRADING` | `ask` | `ask`: one-click dialog before the first signed action of a session. `on`: no dialog. `off`: never sign. |
| `FAIRVENUE_MAX_ORDER_QUANTITY` | `0.01` | Default largest order in BTC. The dialog can change it for the session. |
| `FAIRVENUE_MAX_ORDER_NOTIONAL` | `1000` | Default largest price times size in test USDC. |
| `FAIRVENUE_MAX_PRICE_DEVIATION_BPS` | `500` | Refuse prices further than this from the midpoint, or the mark when one side is empty. |
| `FAIRVENUE_HOME` | `~/.fairvenue/ts2-season2` | Private profile directory (0700). Never point it at an old V4 or season 1 profile. |
| `FAIRVENUE_KEY_FILE` | active imported key | Always use this TS2 key file. It overrides `import_agent_key`. |
| `FAIRVENUE_INSTALLATION_FILE` | `$FAIRVENUE_HOME/installation.json` | Installed deployment pins. |
| `FAIRVENUE_INSTALLATION_SHA256` | packaged pins | SHA256 of a separately reviewed pin file for the same TS2 deployment, for example another approved API origin. A new deployment needs an updated kit. |
| `FAIRVENUE_DOWNLOAD_DIRS` | `~/Downloads` | Where `connect_account` watches for the key. Separate several with `:`. |
| `FAIRVENUE_OPEN_BROWSER` | `on` | `off` makes `connect_account` only return the link, for remote or headless machines. |

`fairvenue-mcp --check` prints the local setup status as JSON without starting the server.

## Safety model

- **Keys stay local.** The server reads key files and signs in its own process. No tool returns,
  logs or journals a seed, signature or signed envelope. Agents never need to open a key file.
- **Deployment pins come from this repository**, verified by SHA256, never from a key file or an
  API reply. `get_market` warns when the served config hash differs from the installed pins.
- **Old keys never become season 2 authority.** A V4 key file is refused with
  "TS2: create a new bot key" and a key of another deployment, such as a TS2 season 1 key, is
  refused too. Neither is ever converted.
- **You decide in a dialog.** Nothing is signed, apart from the no-order connection check and
  recovery lookups, until you click accept in the trading dialog (or set `FAIRVENUE_TRADING=on`).
  The answer lasts for the server session only. The model cannot answer the dialog for you.
- **The browser is opened, never driven.** `connect_account` only opens the key page with your
  system's default browser. You sign in and click yourself.
- **Local guards** refuse orders above the size or notional limits, prices far from the reference,
  prices off the tick and sizes off the step (never rounded), post-only orders that would cross,
  market and FOK orders, and binary floats.
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

- This server does not read balances, positions or fills. The agent relies on its journal (with
  the `order_id` of each order it placed) and public trades, and you can check the Arena
  terminal.
- `accepted` means the exchange journaled the command in soft, operator-trusted state. It is not
  a fill report, an SP1 proof or L1 finality.
- Season 2 proves fills only. A cancel, `cancel_all_orders` or dead man's switch is enforced by
  the operator's matching engine, not by the proof.
- Public reads and signed calls share global rate limits with every participant: roughly 600
  public reads and 600 signed reads a minute, 120 commands a minute in total, and 60 orders and
  account settings a minute per account. Each order costs four signed calls (two context reads,
  the admission and the exact lookup). Let the agent wait for market updates instead of polling.
- Keys expire. `connect_account` notices and opens the key page again.
