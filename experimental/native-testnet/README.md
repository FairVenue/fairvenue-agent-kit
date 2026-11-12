# Experimental native TESTNET client (TS2 season 2)

`fairvenue_schema6.NativeTestnetClient` is a separate experimental protocol adapter for the TS2
native TESTNET. The existing `fairvenue` SDK still targets virtual Arena. The installable skill
has an explicit native branch using this package and a separate legacy V0 branch. Never use a V0
key, bearer token, nonce or virtual balance as native authority.

Version 0.3.0 switched the internals to the TS2 wire. Version 0.4.0 pins TS2 season 2, reads the
server's `not_placed` recovery reasons and adds `account()` (the signed account read),
`submit()` (one context read per command), `last_context()`, a `max_slippage_bps` bound for
MARKET orders and a local refusal while trading is paused. The earlier calls stay the same:
`load_native_testnet_installation`, `NativeTestnetClient.from_credentials`, `snapshot()`,
`run()`, `recover_original()`, `has_pending_request()` and `close()`. Bot keys and grants from
earlier deployments never become season 2 authority. A V4 key file is refused with
"TS2: create a new bot key" and a TS2 season 1 key with `previous_deployment`. Create a new key
instead.

This package does not establish hosted access, working funding, live native liquidity, proof
verification or release readiness. Use it only when the operator has separately approved the
exact native API origin and deployment pins. Do not point it at the current Arena just because
both use a similar URL.

## Pin and install

Use an isolated Python 3.11+ environment. A reviewer must supply an exact commit containing this
package. Do not substitute `main` or an unreviewed tag for that pin. Nothing is published to PyPI.

```sh
: "${REVIEWED_COMMIT:?Set the exact reviewed commit containing the native package}"
git clone https://github.com/FairVenue/fairvenue-agent-kit.git
cd fairvenue-agent-kit
git checkout --detach "$REVIEWED_COMMIT"
test "$(git rev-parse HEAD)" = "$REVIEWED_COMMIT"
python3.12 -m venv .native-venv
. .native-venv/bin/activate
python -m pip install ./experimental/native-testnet
```

The separate distribution is `fairvenue-schema6-agent`. Its import is `fairvenue_schema6`. Its
pinned dependencies and original source hashes are in
`experimental/native-testnet/{pyproject.toml,requirements.txt,SOURCE.json}`. Installation does
not create an account, trade, fund a wallet or activate a server.

## Credentials and independent installation pins

Download a dedicated **TESTNET** bot key with **Create API key & download JSON** in the approved
native Arena flow. The accepted format is `fairvenue-ts2-testnet-agent-v1`: chain
**421614 (Arbitrum Sepolia)**, TS2 season 2 settlement
`0xfd5e17a1a722e0890669aa8c71a024ea4e4277bd` and config hash
`0x416ca6d7eeae85857f387b55e829052eb6474065874774f299e09c0484364bbd`, with the owner-signed
delegation of the key and its Ed25519 seed. These pins bind the supported experimental
deployment, not a mainnet service. Trading agents cannot withdraw. No wallet, deposit or
broadcast helper is exposed here.

A V4 `fairvenue-native-testnet-agent-v1` file raises `AgentError` with code `v4_key` and the
message "TS2: create a new bot key." A TS2 key pinned to another settlement or config hash,
including every TS2 season 1 key (settlement `0x3beb98f3…`), raises `previous_deployment`.
Neither is ever converted.

Keep the downloaded file outside repositories, owner-only (`0600` or `0400`). Give a coding agent
only its local path. Never paste a seed, key file or signed envelope into chat, logs, issues or a
hosted notebook. Do not send it to a GPU worker. Do not print or recreate a missing private key.
One strategy or process owns the key.

Install the operator-approved public installation JSON **independently** of the downloaded
credential. The artifact `fairvenue_schema6/deployments/arbitrum-sepolia-ts2.json` has SHA256
`ba25da81e3ebc9f6679a184993dbc4a64fd089684f54e174e8ad654b725232d4` and binds the TS2 pins and the
HTTPS API origin. The operator must approve its current installation separately. This packaged
public pin is not a hosted-access or release claim. Copy it to a fresh owner-only file without
overwriting any existing installation. Review the hash and provenance before use. The loader
validates a held FD and this hash before HTTP, then the credential loader compares the pins and
API URL against the key file. Neither API responses nor credentials select new pins.

The owner's EIP-712 signature on the delegation is verified by the server. This package has no
secp256k1 dependency: it checks the delegation's exact shape and pins, that the seed derives its
agent key, and that the signed context returns exactly the same grant.

Create a dedicated owner-private (`0700`) continuity directory, separate from credentials and any
other strategy. Its database and fixed sidecars must survive restarts. Do not reset, copy over or
reinitialize an established namespace.

## Start with a no-order dry run

Replace the paths and `approved_api_url` only with separately installed inputs.
`initialize=True` is explicit first installation. Use `False` on every restart. Construction
validates local credentials and creates continuity, without HTTP.

```python
import asyncio
from fairvenue_schema6 import NativeTestnetClient, load_native_testnet_installation


async def no_order_dry_run():
    installed = load_native_testnet_installation(
        "/private/native-installation.json",
        expected_sha256="ba25da81e3ebc9f6679a184993dbc4a64fd089684f54e174e8ad654b725232d4",
    )
    client = NativeTestnetClient.from_credentials(
        "/private/downloaded-testnet-agent.json",
        policy=installed.policy,
        approved_api_url=installed.api_url,
        custody_path="/private/dedicated-continuity/original.sqlite",
        initialize=True,  # FIRST installation only. False on restart.
    )
    try:
        await client.snapshot()  # Signed agent context read only. NO order.
        print("Native context read completed. No order submitted.")
    finally:
        client.close()


asyncio.run(no_order_dry_run())
```

An unavailable, expired, revoked or foreign context stops setup. Do not fall back to V0 or V4,
change the pins, reset nonces or infer balance from a funding receipt. Do not print the returned
grant or context object. A successful dry run is not permission to trade. Obtain separate
strategy and bounded-order authorization. Monetary action inputs stay exact decimal strings,
never Python binary floats.

## Trading on TS2

`snapshot()` returns the signed TS2 context (account fee mode, leverage, open batch, admission),
the public grant and `next_nonce`. `run({"nonce": snapshot["next_nonce"], "expiresAtMs": ...,
"action": ...})` signs one request locally with one Ed25519 signature over its exact native bytes.
Those bytes, the order id and the request digest match the shared TS2 JS and Rust vectors.
`submit(action, lifetime_ms=...)` is `run` with one signed context read: it takes the nonce from
that read and an expiry `lifetime_ms` ahead (23 hours at most), never past the key's expiry.

- `place` keeps its order shape: `symbol`, `side`, `order_type`, `time_in_force`, `price`,
  `quantity`, `reduce_only` and `post_only`. Price and quantity must be whole ticks and lots of the
  public market (`tick_size` and `step_size`, read once from `/api/v1/info` `native_market` for
  the pinned config hash). Off-grid input raises `invalid_order` before signing and is never
  rounded. `LIMIT` takes `GTC`, `IOC` or `FOK`. A `MARKET` order (`IOC` or `FOK`) signs a
  protection bound, its price rounded away from the book, or with `price` `None` a bound
  `max_slippage_bps` (an int from 1 to 1000, default 50, the browser's 0.5%) beyond the best
  opposite price, or beyond the mark when that side is empty. `expiresAtMs` is the order's
  lifetime: up to 23 hours for `GTC` and about 30 seconds for one-shot orders, never past the
  key's expiry. Every order carries the account's fee mode and a fresh random client nonce.
- `cancel` takes the 0x `order_id` from the order's ACK (`result["ack"]["order_id"]`).
- `kill_all` cancels every resting and FairLag-held order of the account at once.
- `fee_mode`, `leverage` (`"1"` to `"100"`) and `dead_man_switch` are account commands with one
  strictly increasing command nonce per account and key. `next_nonce` is that nonce and never
  falls below one this key already signed. The fee mode changes only with no position and no
  working orders. Leverage can rise at any time and fall only with no position and no working
  orders. The operator can keep fee mode and leverage closed. Then they raise `admission_closed`
  before anything is signed.
- `modify` raises `unsupported`. Cancel the order and place a new one.

While the signed context shows admission closed, `run` and `submit` sign only cancels, kill-all
and, while the mark catches up (`mark_lagging`), reduce-only orders. Anything else raises
`admission_closed` before anything is signed. `last_context()["admission"]["reasons"]` says why,
for example `funding_owed` (only cancels work until a funding payment is applied).

## Reading the account

`await client.account()` is the bot-signed account read on `/v1/ts2/agent/read`. It signs
`FV_TS2_READ_SIG\0 || config_hash || 1 || u64be account || 32 zero bytes || u64be expiry` with a
30 second expiry (the server accepts at most 60), uses no nonce and places no order. It returns
`{"context", "view"}`: the signed context (fee mode, leverage, admission, or `None` with
`with_context=False`) and the strict view, parsed with exact decimal strings:

- `account`: wallet balance, reserved order margin, locked position margin, realized PnL,
  funding paid, position quantity and entry price (`None` before the account is funded).
- `orders` and `fills`: the latest 64 each, prices and sizes converted from ticks and lots,
  each fill with its settlement state (`operator_soft`, `operator_sealed`, `not_settled` or
  `controller_observed_settled` and `_final` with the settle transaction).
- `market`: tick, step, FairLag, the 20-level book and the mark. FairLag-held orders show only
  their id and `pending` status, never side, price or size.

It works while an original is pending. It is soft, operator-trusted state, never proof or
finality.

A successful `run()` returns the operator journal's ACK (`fairvenue-ts2-agent-ack-v1`, bound to
the request digest and, for an order, to its own order id) and the exact-original lookup that
retired it. An ACK is not a fill, settlement or finality. Matching follows after FairLag.

Season 2 proves fills only. The ZK proof checks every executed fill: its signed order and
delegation, limit price, expiry, fees, margin and balances. Cancels, kill-all and the dead man's
switch are enforced by the operator's matching engine and journal, not by the proof. A cancel ACK
therefore carries no kill receipt (`digest` and `signature` are null and `kill_seq` is `"0"`).

## Exact-original recovery

Before admission, the client durably retains one bounded **public** signed TS2 request. It does
not store seeds, Google data, cookies or CSRF values there. A timeout, process failure or lost
acknowledgement leaves that original unresolved.

On restart, open the **same** continuity database with `initialize=False` and call
`await client.recover_original()` inside the same event loop. This is a lookup with the exact
original body, not a resend. Only the exact original's own lookup answer can retire it:
`accepted` (journaled), `rejected` (journaled native rejection), `not_placed` (never journaled
and past its own expiry, wire reason `expired_never_admitted`) or `not_settled` (its batch was
voided, it never takes effect). The result's `reason` is the server's fixed `not_placed` reason,
else `None`. `unresolved` stays retained. Stop and ask the operator to reconcile, never send
another order, invent a replacement, expire or delete the original. An advanced nonce, increased
balance, new batch or your own clock is not evidence. Only the server's answer is. If the original
never reached the server, the lookup answers `unresolved` until its own expiry has passed, so new
signing waits that long (30 seconds for one-shot orders and account commands, the order's
lifetime for `GTC`).

`run()` itself also clears the original when the server answers a definitive `not_placed`
refusal bound to that exact request (HTTP 409, or 429 for `rate_limited`, refused before any
durable effect). It then raises `AgentError` with the reason as `code`: `rejected`,
`invalid_request`, `admission_closed` or `rate_limited`. Build a fresh request from a new
`snapshot()`. Any other failure is `outcome_unknown` and needs `recover_original()`.

Soft acceptance is operator-trusted, not independently established L1 finality or proof
verification. This SDK does not itself verify an SP1 proof or make a hosted service ready.
Offline tests are synthetic. Genuine hosted funding and trading, GPU proof, onchain publication,
independent replay, restart and exit acceptance remain separate release gates.

## Optional MCP server

The `mcp` extra adds `fairvenue_mcp`, a local stdio MCP server over this client for coding
agents: `pip install './experimental/native-testnet[mcp]'` provides the `fairvenue-mcp`
command. It is a kit extension, not imported protocol source. It never changes the protocol
modules, signs no order or account setting until the user allows trading in a one-click dialog,
or on a one-shot `127.0.0.1` browser page when the client cannot show the dialog (or
`{"trading": "on"}` in an owner-only `~/.fairvenue/settings.json`, or `FAIRVENUE_TRADING=on`,
which wins over the file), moves downloaded keys into an owner-private profile without
returning their seed, reads the account with `account()`, signs with `submit()` and uses
`recover_original()` for unknown outcomes. Installation and tools:
[docs/MCP.md](../../docs/MCP.md).

## Offline checks and source parity

The whole `fairvenue_schema6` package, the deployment pins, the requirements, the TS2 tests and
their fixtures are byte-identical imports from the operator-managed testnet implementation.
`SOURCE.json` records their hashes. The TS2 fixtures are the shared client vectors of the TS2
JS codec and the Rust adapter. Golden fixtures are package-local, public and synthetic, with TEST
keys only. No private sibling checkout is needed. Older `AgentClient` and `BotContextProvider`
retain their source-closed gates. Installing this package never unlocks them.

From the agent-kit checkout, with exact requirements already installed:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experimental/native-testnet python -m unittest discover -s experimental/native-testnet/tests -v
python -m build experimental/native-testnet
```

Root Ruff checks exclude the immutable imported protocol modules and copied tests. The MCP
server and its tests are linted there and type-checked by `mypy` in this directory. The separate
native CI job runs all offline tests, including the MCP tests, and builds the package. No
existing V0 test or SDK source is changed or disabled.
