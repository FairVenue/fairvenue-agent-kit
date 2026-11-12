# FairVenue Agent Kit

Build, test and run trading agents on FairVenue with an installable skill, an MCP server, typed
Python SDKs, API documentation and runnable examples.

The kit targets two explicitly separate environments. The **native TESTNET** is an
operator-run, custodial Arbitrum Sepolia deployment with mock USDC: real native signatures, no
real funds. The **legacy virtual Arena** (the root `fairvenue` SDK) is a virtual-settlement
simulator with no deposits, withdrawals or real assets. Neither routes user orders or
credentials to an external venue.

Status: early testnet tooling, not a live trading service. Pin a reviewed commit before relying on
it. This repository does not include the FairVenue backend or grant hosted access.

**TS2 season 2 (version 0.4.0).** The native TESTNET now runs TS2 season 2, a fresh deployment
with new pins. Version 0.4.0 of the MCP server and the native SDK speaks it, and earlier
versions cannot connect. Update once and create one new key:

- Claude Code: `claude plugin marketplace update fairvenue`, then
  `claude plugin update fairvenue@fairvenue`, then restart Claude Code.
- Codex: `codex plugin marketplace upgrade`, then `codex plugin remove fairvenue@fairvenue` and
  `codex plugin add fairvenue@fairvenue`, then restart Codex.
- Other clients: see [docs/MCP.md](docs/MCP.md#update-and-create-a-new-key).
- Then ask your agent to connect. Sign in on the key page and click
  **Create API key & download JSON**. Keys from season 1 and the older V4 testnet are refused.
  Your account gets 1,000 test USDC once in season 2, right after sign-in.

New in 0.4.2: `~/.fairvenue/settings.json` is a trading switch that works in every client,
including Codex, which never passes `FAIRVENUE_TRADING` from your shell to plugin servers. When
your client cannot ask you, the agent turns it on with one command you approve, and
`rm ~/.fairvenue/settings.json` turns it off. See
[docs/MCP.md](docs/MCP.md#the-trading-switch-file).

New in 0.4.1: a client that cannot really show the trading dialog (Codex answered it without
showing anything) now gets a confirmation page in your browser instead of a refusal. See
[docs/MCP.md](docs/MCP.md#allowing-trading).

New in 0.4.0: `get_account` (balances, margin, PnL, position, open orders and fills), MARKET
orders with a slippage bound, FOK, `close_position`, plain-word reasons when trading is paused,
and one signed context read per order. `modify_order`, `install_deployment_pins` and
`verify_connection` are gone. Season 2 proves fills only. Cancels, cancel-all and the dead man's
switch are enforced by the operator's matching engine, not by the proof.

## Start with your coding agent

Install the plugin: the `fairvenue` skill teaches your agent how FairVenue works, and the MCP
server lets it act on the native TESTNET. In Claude Code and Codex it is one command and the
recommended path. In other clients the MCP server is optional. For hackathon prizes, use it to
explore and build, then run a standalone bot with the native SDK (prizes need a full UTC day of
hourly returns).

**Claude Code**

```text
/plugin install fairvenue --marketplace FairVenue/fairvenue-agent-kit
```

On versions before 2.1.275: `/plugin marketplace add FairVenue/fairvenue-agent-kit`, then
`/plugin install fairvenue@fairvenue`.

**Codex**

```bash
codex plugin marketplace add FairVenue/fairvenue-agent-kit
codex plugin add fairvenue@fairvenue
```

**Cursor, VS Code, Claude Desktop and other agents:** add the skill with
`npx skills add FairVenue/fairvenue-agent-kit --skill fairvenue` and the MCP server from
[docs/MCP.md](docs/MCP.md). The server needs [uv](https://docs.astral.sh/uv/).

Then ask your agent:

```text
Connect me to the FairVenue TESTNET.
```

You only click. The agent opens the key page in your browser, where you sign in with Google and
click **Create API key & download JSON**. It picks the key up by itself, stores it privately,
checks the connection without placing an order and reads your account and the market. The first
time it signs an order, you get one dialog to allow trading for the session, with the per-order
limits filled in. If your client cannot show that dialog, the same question opens as a page in
your browser with **Allow** and **Deny**, and if that is not possible either, the agent switches
trading on for this computer with a command you approve. Every signed request is retained until its outcome is known, and nothing new
is signed meanwhile, so a timeout leads to a recovery lookup instead of a blind resend. The MCP
server runs on macOS and Linux (Windows through WSL). WSL and remote machines need one setting,
see [docs/MCP.md](docs/MCP.md#wsl-and-remote-machines).

What the agent gets:

- **Skill:** setup, safety rules, exact-original recovery, the hackathon rules and how to build a
  standalone bot. See [skills/fairvenue/SKILL.md](skills/fairvenue/SKILL.md).
- **MCP tools:** `connect_account`, `get_account`, `get_market`, `place_order` (LIMIT or MARKET),
  `close_position`, `cancel_order`, `cancel_all_orders`, `set_dead_man_switch`,
  `recover_pending_request` and more, plus the prompts `onboard`, `trading_session`, `build_bot`
  and `hackathon_plan`. See [docs/MCP.md](docs/MCP.md).

The repository is a Claude Code plugin and marketplace (`.claude-plugin/`) and a portable
[Agent Plugin](https://agent-plugins.org) (`plugin.json`, `mcp.json` and `skills/`).

## Bot Building Hackathon

Scoring days are **November 16, 17 and 18, 2026 (UTC)**. Each day is scored separately, and the
rankings within those days decide the winners. It runs only with 50+ participants. Only API
trades count: trades signed with a bot
key from **Create API key & download JSON**, including trades your agent signs through the MCP
server. Keys from before TS2 season 2 no longer sign, so create a new one. Full rules: [fairvenue.xyz/hackathon/rules](https://fairvenue.xyz/hackathon/rules) and
[skills/fairvenue/references/hackathon.md](skills/fairvenue/references/hackathon.md). Bug reports
and testnet analyses go in a GitHub issue titled `[Hackathon bug]` or `[Hackathon analysis]`.

## Native TESTNET SDK

[`experimental/native-testnet`](experimental/native-testnet/README.md) provides
`fairvenue_schema6.NativeTestnetClient` for standalone bots: independently reviewed TS2 season 2
deployment pins, a downloaded TS2 bot key (`fairvenue-ts2-testnet-agent-v1`), a no-order dry run
and exact-original recovery. Its signing bytes, order ids and request digests match the shared
TS2 JS and Rust vectors. The MCP server is an optional extra of the same package
(`fairvenue-schema6-agent[mcp]`). The native package does not accept V0, V4 or TS2 season 1
credentials and does not establish hosted funding, liquidity, proof verification or release
readiness.

## Legacy virtual Arena SDK

Everything below is the root `fairvenue` package for the legacy virtual-settlement Arena. Do not
use it, its keys or its endpoints for the native TESTNET. The hosted `app.fairvenue.xyz` Arena now
runs the native TESTNET, so this SDK needs a local or operator-provided V0 backend. Keys from the
hosted key page are native TESTNET bot keys, which this SDK cannot use.

### Install

```bash
git clone https://github.com/FairVenue/fairvenue-agent-kit.git
cd fairvenue-agent-kit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The Python import and CLI remain `fairvenue`. This package is not published to PyPI; install from
this checkout, not an unrelated package with a similar name.

### Local account

Run the Arena backend on `127.0.0.1:8088`, then create an owner-only credential file:

```bash
fairvenue init-local \
  --base-url http://127.0.0.1:8088 \
  --credentials .fairvenue/testnet.json

fairvenue verify --credentials .fairvenue/testnet.json
```

`init-local` refuses remote hosts, refuses to overwrite an existing file, and writes credentials
with mode `0600`. It never prints the private key. Users of an operator-provided V0 Arena create a
separate bot agent credential from its **System** panel and must not call local reset/faucet
endpoints.

### Read public market data

```python
from fairvenue import FairVenueClient

with FairVenueClient("http://127.0.0.1:8088") as client:
    print(client.info({"type": "bbo", "market_id": 0}))
```

### Submit a signed post-only order

```python
from fairvenue import Credentials, FairVenueClient

credentials = Credentials.from_file(".fairvenue/testnet.json")

with FairVenueClient.from_credentials(credentials) as client:
    accepted = client.place_limit(
        market_id=0,
        side="buy",
        price="70000.0",
        size="0.001",
        time_in_force="Alo",
        client_order_id=client.new_client_order_id(),
    )
    result = client.wait_for_transaction(accepted["response"]["tx_hash"])
    print(result)
```

`accepted` means the command is durable. It does not mean the order is open or filled. Read the
transaction result or private stream before updating strategy state.

### Examples

- `examples/public_market_data.py` — metadata and current BBO, no credentials.
- `examples/place_post_only.py` — safe post-only example; submission requires
  `FAIRVENUE_SUBMIT=1`.
- `examples/protected_ioc.py` — converts maximum slippage into an IOC protection price.
- `examples/stream_order_book.py` — public book stream; stops safely on a sequence gap.
- `docs/AI_AGENT_PROMPT.md` — a copy-paste prompt for a coding agent.

### Protocol invariants

- Monetary values stay decimal strings or `Decimal`, never binary floats.
- Actions use RFC 8785 JCS and Ed25519 with FairVenue domain separation.
- Nonces are unique per agent key and requests expire quickly.
- There is no unbounded market-order wire type.
- Retriable logical orders use stable `client_order_id` values.
- WebSocket book gaps require a fresh snapshot.
- Bearer tokens authorize private reads only; every trading action is separately signed.
- Secrets, tokens, envelopes and private events must not be logged.

See [`docs/QUICKSTART.md`](docs/QUICKSTART.md), [`docs/API_V1.md`](docs/API_V1.md),
[`docs/SIGNING.md`](docs/SIGNING.md), and [`docs/WEBSOCKET.md`](docs/WEBSOCKET.md).

## Verify

```bash
ruff format --check .
ruff check .
mypy src
pytest
python -m build
```

Native SDK and MCP server, with `./experimental/native-testnet[mcp]` installed:

```bash
(cd experimental/native-testnet && mypy)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experimental/native-testnet python -m unittest discover -s experimental/native-testnet/tests -v
python -m build experimental/native-testnet
```
