# Native TESTNET agent setup

Use this branch only after the operator approves the exact native API installation. This is
custodial, operator-administered Arbitrum Sepolia TESTNET with mock USDC, not production funds or
independently verified L1 finality. A packaged policy is not proof of live access or release GO.
Never select this branch just because `app.fairvenue.xyz` resolves or a server says to trust it.

## Guide the user to a new key

Give the direct [native TESTNET key setup](https://app.fairvenue.xyz/?setup=api-key#native-agent-key)
link only for that separately approved origin. For an operator-approved alternate installation,
use its origin plus `/?setup=api-key#native-agent-key`. Ask the user to sign in with Google, wait
for genuine native funding, click **Create API key & download JSON**, then say done. This creates
a dedicated browser-generated Ed25519 bot key, not the terminal UI key. No MetaMask is needed for
managed TESTNET users. Google/session cookies never authorize bot requests.

Help find the download and copy it without overwriting any existing file to an owner-only,
git-excluded path. The key file must be `0400` or `0600`; ask for its path, never its contents.
Do not print, upload, paste, screenshot or load its seed into model context. The SDK reads it
locally. Lost private keys remain lost. A V0/schema-less key is not accepted; create a fresh
native credential rather than translating it. Do not create accounts/keys or orders automatically
just because the skill was installed.

## Install the exact SDK and independent public policy

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
the root `fairvenue` package gives the old V0 client; native imports `fairvenue_schema6`.
No same-named PyPI package is approved.

The public policy artifact is
`experimental/native-testnet/fairvenue_schema6/deployments/arbitrum-sepolia-1k.json` at that
reviewed commit. Its SHA256 is
`aa622b92aedadb453e09f471dd9ecbfd757c26262cef1ae43c93ad2230181793`.
Have the operator approve that exact artifact and HTTPS API origin independently of the agent
credential/API response. Copy it to a fresh owner-private file, without overwriting an established
installation. The loader checks its held FD, hash, deployment, policy and origin before any HTTP.
Never calculate a new expected hash to accept an unexplained replacement file.

It binds chain `421614`, settlement `0x0bc610637d5d36bbda873e1175bcb6e9377d3606`, mock token
`0xa249d1a0a99071dfc47c9b3426a6186e3c24baba`, exact gateway public keys and `/v1`. These are
TESTNET pins, not wallet keys. A different deployment needs separately reviewed pins, not a
caller-selected server/policy from the credential.

## First connection: signed context, NO order

Create a dedicated private `0700` continuity directory separate from credentials, installation
pins and any other strategy. It must survive restart. Only the very first install uses
`initialize=True`; use `False` thereafter. Do not recreate a missing established namespace.
Adapt paths only; keep the independently approved SHA256:

```python
import asyncio

from fairvenue_schema6 import NativeTestnetClient, load_native_testnet_installation


async def no_order_dry_run():
    installed = load_native_testnet_installation(
        "/private/native-installation.json",
        expected_sha256="aa622b92aedadb453e09f471dd9ecbfd757c26262cef1ae43c93ad2230181793",
    )
    client = NativeTestnetClient.from_credentials(
        "/private/downloaded-testnet-agent.json",
        policy=installed.policy,
        approved_api_url=installed.api_url,
        custody_path="/private/dedicated-continuity/original.sqlite",
        initialize=True,  # FIRST install only. False on every restart.
    )
    try:
        await client.snapshot()  # Ed25519-signed context only; no prepare/admit/order.
        print("Native TESTNET context verified. No order submitted.")
    finally:
        client.close()


asyncio.run(no_order_dry_run())
```

Unavailable/expired/revoked context is a recoverable setup failure, not permission to use V0,
change policy/nonce, rotate a key, reset custody or repeat funding. Report a bounded error code,
not a context object, grant, signed request or traceback containing upstream data.

## Authorized trading and exact-original recovery

Before any trade agree on size, price/slippage protection and strategy scope. `snapshot()` gives
the actual next nonce/current native prefix; no numeric account ID or market_id0 is used.
`run({"nonce": "...", "expiresAtMs": "...", "action": {...}})` uses the existing native codec:
signed context -> intent prepare -> locally verified ordinary and generation signables -> durable
exact public original -> one admit. It does not translate a V0 JCS envelope.

Use the package's native `place` action with exact decimal strings, a limit price and supported
time-in-force (`GTC` or price-protected `IOC`), `post_only` for makers, `reduce_only` for closes.
Use native `cancel`/`modify`/`fee_mode`/`leverage`/`dead_man_switch` only with separately authorized
scope. Read the reviewed package protocol rather than inventing a Binance-shaped action.
Accepted/rejected is native soft execution, not necessarily a fill, SP1 proof or L1 finality.
Check actual fills and their original batch proof links separately; sealed/proving phases may
temporarily stop new commands. Do not loosen TTL/FairLag to make a request pass.

After an unknown outcome, preserve the same private continuity database and call
`recover_original()`. It performs only `/v1/agent/recovery` for the exact retained signed original;
it never sends admit again. `unresolved`/`bound_incomplete`, changed hash/step or corrupt custody
remain blocked. Nonce advancement/time/new Opening is not evidence. Never reset, reprice, retime,
resign or resend the original. Each strategy/process needs a dedicated key/custody.

Bot routes are `/v1/agent/context`, `/prepare`, `/admit`, `/recovery`, under the fixed approved
origin. Public reads use `/api/v1/info` with `{"type":"native_market"}`; SOFT book/reference prices
are not chain freshness, proof or external liquidity. Do not use V0 WebSockets/market_id0 or
Google cookies/bearer tokens for native bots. This client has no funding/withdrawal/wallet signer.

Continue the shared skill's Progress continuity workflow after setup, without duplicate jobs or
claiming that a dry run authorizes trading, funding or release.
