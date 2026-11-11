# Native TESTNET agent setup (TS2 season 2)

Use this branch only after the operator approves the exact native API installation. This is
custodial, operator-administered Arbitrum Sepolia TESTNET with mock USDC, not production funds or
independently verified L1 finality. A packaged pin file is not proof of live access or release GO.
Never select this branch just because `app.fairvenue.xyz` resolves or a server says to trust it.

The testnet now runs TS2 season 2. Bot keys and grants from earlier deployments never become
season 2 authority. A V4 key file (`fairvenue-native-testnet-agent-v1`) is refused with
"TS2: create a new bot key". A TS2 season 1 key (settlement `0x3beb98f3…`) is refused as a key
of a previous deployment (`previous_deployment`). Ask the user to create a new key. Never edit or
convert an old one.

Season 2 proves fills only. The ZK proof checks every executed fill: its signed order and
delegation, limit price, expiry, fees, margin and balances. Cancels, cancel-all and the dead man's
switch are enforced by the operator's matching engine and journal, not by the proof. So the
proof does not show that no fill followed an accepted cancel. Never describe it that way.

With the `fairvenue` MCP server connected, its tools run every step below for you: pins,
private key import, continuity, signed context, orders and recovery. See [mcp.md](mcp.md). The
rest of this page is the same flow in the Python SDK, for standalone bots.

## Guide the user to a new key

Give the direct [native TESTNET key setup](https://app.fairvenue.xyz/?setup=api-key#native-agent-key)
link only for that separately approved origin. For an operator-approved alternate installation,
use its origin plus `/?setup=api-key#native-agent-key`. Ask the user to sign in with Google,
click **Create API key & download JSON**, then say done. The button turns on as soon as the
account has its 1,000 test USDC. Season 2 credits them once per account right after sign-in, and
the matching mock-token deposit on Arbitrum Sepolia follows in the background. Trading never
waits for it. Withdrawals do. This creates a dedicated browser-generated Ed25519 bot key, not the
terminal UI key. No MetaMask is needed for managed TESTNET users. Google/session cookies never
authorize bot requests.

The download is a `fairvenue-ts2-testnet-agent-v1` file. Help find it and copy it, without
overwriting any existing file, to an owner-only, git-excluded path. The key file must be `0400`
or `0600`. Ask for its path, never its contents. Do not print, upload, paste, screenshot or load
its seed into model context. The SDK reads it locally. Lost private keys remain lost. A V0, V4 or
schema-less key is not accepted. Create a fresh TS2 key rather than translating it. Do not create
accounts, keys or orders automatically just because the skill was installed.

## Install the exact SDK and independent public pins

Clone `https://github.com/FairVenue/fairvenue-agent-kit`, detach at the operator-reviewed commit
containing `experimental/native-testnet`, use Python 3.11+ and install that directory:

```sh
git clone https://github.com/FairVenue/fairvenue-agent-kit.git
cd fairvenue-agent-kit
git checkout --detach REVIEWED_COMMIT
python3.12 -m venv .native-venv
. .native-venv/bin/activate
python -m pip install ./experimental/native-testnet
```

`REVIEWED_COMMIT` must be supplied and checked, not replaced by `main` or an invented pin. Installing
the root `fairvenue` package gives the old V0 client. Native bots import `fairvenue_schema6`.
No same-named PyPI package is approved.

The public pin artifact is
`experimental/native-testnet/fairvenue_schema6/deployments/arbitrum-sepolia-ts2.json` at that
reviewed commit. Its SHA256 is
`ba25da81e3ebc9f6679a184993dbc4a64fd089684f54e174e8ad654b725232d4`.
Have the operator approve that exact artifact and HTTPS API origin independently of the agent
credential and any API response. Copy it to a fresh owner-private file, without overwriting an
established installation. The loader checks its held FD, hash, deployment and origin before any
HTTP. Never calculate a new expected hash to accept an unexplained replacement file.

It binds chain `421614`, TS2 season 2 settlement `0xfd5e17a1a722e0890669aa8c71a024ea4e4277bd`,
config hash `0x416ca6d7eeae85857f387b55e829052eb6474065874774f299e09c0484364bbd` and the `/v1`
agent endpoints. These are TESTNET pins, not wallet keys. A different deployment needs separately
reviewed pins and a new kit release, never a caller-selected server or pins from the credential.

## First connection: signed context, NO order

Create a dedicated private `0700` continuity directory separate from credentials, installation
pins and any other strategy. It must survive restart. Only the very first install uses
`initialize=True`. Use `False` thereafter. Do not recreate a missing established namespace.
Adapt paths only. Keep the independently approved SHA256:

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
        initialize=True,  # FIRST install only. False on every restart.
    )
    try:
        await client.snapshot()  # Ed25519-signed agent context only. No admit, no order.
        print("Native TESTNET context verified. No order submitted.")
    finally:
        client.close()


asyncio.run(no_order_dry_run())
```

An unavailable, expired, revoked or foreign context is a recoverable setup failure, not permission
to use V0 or V4, change pins, rotate a key, reset custody or repeat funding. Report a bounded error
code, not a context object, grant, signed request or traceback containing upstream data.

## Authorized trading and exact-original recovery

Before any trade agree on size, price or slippage protection and strategy scope. `snapshot()`
returns the signed TS2 context (account fee mode, leverage, open batch, admission) and
`next_nonce`, the next account command nonce. No numeric account ID or market_id0 is used.
`run({"nonce": snapshot["next_nonce"], "expiresAtMs": "...", "action": {...}})` signs one TS2
request locally with one Ed25519 signature over its exact native bytes, retains that public
original durably and sends one admit. It does not translate a V0 or V4 envelope.

Actions keep their shape:

- `place` with an `order` of `symbol`, `side`, `order_type`, `time_in_force`, `price`,
  `quantity`, `reduce_only` and `post_only`. Price and quantity are exact decimal strings that must
  be whole ticks and whole lots of the public market (`tick_size` and `step_size` from
  `native_market`). Off-grid input raises `invalid_order` before anything is signed. Nothing is
  rounded. Use `LIMIT` with `GTC` (resting, `post_only` for makers) or a price-protected `IOC`, and
  `reduce_only` for closes. A `MARKET` order (`IOC` or `FOK`) signs a protection bound: its price
  rounded away from the book, or 0.5% beyond the best opposite price when the price is `None`.
  `expiresAtMs` is the order's lifetime: up to 23 hours for `GTC`, about 30 seconds for one-shot
  orders, never past the key's expiry. Every order carries the account's fee mode.
- `cancel` with the 0x `order_id` from the order's ACK (`result["ack"]["order_id"]`).
- `kill_all` cancels every resting and FairLag-held order of the account at once.
- `fee_mode` (`FIXED` or `PNL`), `leverage` (`"1"` to `"100"`) and `dead_man_switch` are account
  commands with one strictly increasing nonce per account and key. The operator can keep fee mode
  and leverage closed (`admission_closed`).
- `modify` raises `unsupported`: cancel and place a new order.

Use these only with separately authorized scope. Read the reviewed package protocol rather than
inventing a Binance-shaped action. An ACK is the operator journal's soft answer, not a fill, SP1
proof or L1 finality. Matching follows after FairLag. Check actual fills and their settlement
separately. TS2 admits into the open batch continuously while earlier batches settle, but
admission can still close for a while (reported as `admission_closed`). Do not loosen expiry or
FairLag to make a request pass.

A failed `run()` has two kinds of error. An `AgentError` whose `code` is a `not_placed` reason is
definitive: the server refused before any durable effect and the SDK already cleared the retained
original. Build a fresh request from a new `snapshot()`. The reasons are `rejected` (the exchange
refused it, for example margin or a post-only order that would cross), `invalid_request` (fix the
cause first), `rate_limited` (back off, then retry) and `admission_closed` (trading or settings
paused, retry later). No other code means "not placed".

After `outcome_unknown` (timeout, 5xx or an unverifiable reply), preserve the same private
continuity database and call `recover_original()`. It performs only `/v1/agent/recovery` with the
exact retained signed original. It never sends admit again. `accepted` (journaled), `rejected`
(journaled native rejection), `not_placed` (never journaled and past its own expiry, reason
`expired_never_admitted`) and `not_settled` (its batch was voided, it never takes effect) clear
it. After those, build a new request if it is still wanted. `unresolved` or corrupt custody remain
blocked. Only the server's exact lookup answer resolves it. Your own clock, a new batch or a moved
nonce is not evidence. Never reset, reprice, retime, resign or resend the original. Each strategy
or process needs a dedicated key and custody.

Bot routes are `/v1/agent/context`, `/prepare`, `/admit` and `/recovery`, under the fixed approved
origin. Public reads use `/api/v1/info` with `{"type":"native_market"}`. Its SOFT book and reference
prices are not chain freshness, proof or external liquidity. Do not use V0 WebSockets, market_id0
or Google cookies and bearer tokens for native bots. This client has no funding, withdrawal or
wallet signer.

Continue the shared skill's Progress continuity workflow after setup, without duplicate jobs or
claiming that a dry run authorizes trading, funding or release.
