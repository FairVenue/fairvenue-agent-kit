# Experimental native TESTNET client

`fairvenue_schema6.NativeTestnetClient` is a separate experimental protocol adapter.
The existing `fairvenue` SDK still targets virtual Arena. The installable skill has an explicit
native branch using this package and a separate legacy V0 branch.
Never use a V0 key, bearer token, nonce or virtual balance as native authority.

This package does not establish hosted access, working funding, live native
liquidity, proof verification or release readiness. Use it only when the operator
has separately approved the exact native API origin, public policy and deployment.
Do not point it at the current Arena just because both use a similar URL.

## Pin and install

Use an isolated Python 3.11+ environment. A reviewer must supply an exact commit
containing this package; the older public main commit does not contain it.
Do not substitute `main` or an unreviewed tag for that pin. Nothing is published to PyPI.

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

The separate distribution is `fairvenue-schema6-agent`; its import is
`fairvenue_schema6`. Its pinned dependencies and original source hashes are in
`experimental/native-testnet/{pyproject.toml,requirements.txt,SOURCE.json}`.
Installation does not create an account, trade, fund a wallet or activate a server.

## Credentials and independent installation pins

Download a dedicated **TESTNET** bot credential through the separately approved
native Arena flow. The accepted format is `fairvenue-native-testnet-agent-v1`,
chain **421614 (Arbitrum Sepolia)**, settlement
`0x0bc610637d5d36bbda873e1175bcb6e9377d3606`, and mock token
`0xa249d1a0a99071dfc47c9b3426a6186e3c24baba`.
These addresses bind the supported experimental deployment, not a mainnet service.
Trading agents cannot withdraw. No wallet/deposit/broadcast helper is exposed here.

Keep the downloaded file outside repositories, owner-only (`0600` or `0400`).
Give a coding agent only its local path; never paste a seed, key file or signed
envelope into chat, logs, issues or a hosted notebook. Do not send it to a GPU worker.
Do not print or recreate a missing private key. One strategy/process owns the key.

Install the operator-approved public installation JSON **independently** of the downloaded
credential. The artifact `fairvenue_schema6/deployments/arbitrum-sepolia-1k.json` has SHA256
`aa622b92aedadb453e09f471dd9ecbfd757c26262cef1ae43c93ad2230181793` and binds the exact native
policy and HTTPS API origin. The operator must approve its current installation separately;
this packaged public pin is not a hosted-access or release claim. Copy it to a fresh owner-only
file without overwriting any existing installation. Review the hash/provenance before use.
The loader validates a held FD and this hash before HTTP, then the credential loader compares
the policy/API against the key file. Neither API responses nor credentials select new pins.

Create a dedicated owner-private (`0700`) continuity directory, separate from
credentials and any other strategy. Its database and fixed sidecars must survive
restarts. Do not reset, copy over or reinitialize an established namespace.

## Start with a no-order dry run

Replace the paths and `approved_api_url` only with separately installed inputs.
`initialize=True` is explicit first installation; use `False` on every restart.
Construction validates local credentials and creates continuity, without HTTP.

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
        initialize=True,  # FIRST installation only; False on restart
    )
    try:
        await client.snapshot()  # Signed native context read only; NO order.
        print("Native context read completed. No order submitted.")
    finally:
        client.close()


asyncio.run(no_order_dry_run())
```

An unavailable, expired, revoked or foreign context stops setup. Do not fall back
to V0, change the policy, reset nonces or infer balance from a funding receipt.
Do not print the returned account/grant/context object. A successful dry run is
not permission to trade; obtain separate strategy and bounded-order authorization.
Monetary action inputs stay exact decimal strings, never Python binary floats.

## Exact-original recovery

Before admission, the client durably retains one bounded **public** signed native
request and its recorded protocol time. It does not store seeds, Google data,
cookies, CSRF values or private session markers there. A timeout, process failure
or lost acknowledgement leaves that original unresolved.

On restart, open the **same** continuity database with `initialize=False` and call
`await client.recover_original()` inside the same event loop. This is a lookup,
not a resend. Only exact authenticated native accepted/rejected history can
retire the original. An advanced nonce, increased balance, new root, elapsed time
or failed UI indication is insufficient. Unresolved/bound-incomplete outcomes
stay retained: stop and ask the operator to reconcile, never send another order,
invent a replacement hash, fee-bump, expire or delete the original.

Soft acceptance is operator-trusted execution, not independently established
Nitro/L1 finality or proof verification. This SDK does not itself verify an SP1
proof or make a hosted service ready. Offline tests are synthetic; genuine hosted
funding/trading, GPU proof, onchain publication, independent replay, restart and
exit acceptance remain separate release gates.

## Offline checks and source parity

The protocol modules, original build metadata and requirements are byte-identical
imports from the operator-managed testnet implementation. `SOURCE.json` records
their hashes. Golden fixtures are package-local, public and synthetic; no private
sibling checkout is needed. Older `AgentClient` and `BotContextProvider` retain
their source-closed gates. Installing this package never unlocks them.

From the agent-kit checkout, with exact requirements already installed:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experimental/native-testnet python -m unittest discover -s experimental/native-testnet/tests -v
python -m build experimental/native-testnet
```

Root Ruff checks explicitly exclude this immutable imported source subtree. Its
separate native CI job runs all offline tests and builds the package. No existing
V0 test, SDK source or skill is changed or disabled.
