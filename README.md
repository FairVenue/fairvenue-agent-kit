# FairVenue Agent Kit

Build and test a trading agent with an installable skill, a typed Python SDK, API documentation
and runnable examples.

This kit connects to FairVenue Arena, a virtual-settlement testnet. There are no
deposits, withdrawals, real assets or live external execution. External BTCUSDC data is reference
context only; user orders remain simulated inside FairVenue.

Status: early testnet tooling, not a live trading service. Pin a reviewed commit before relying on
it and verify server metadata at startup. This repository does not include the Arena backend or
grant hosted access.

## Start with your coding agent

Install the `fairvenue` skill into your strategy project:

```bash
npx skills add FairVenue/fairvenue-agent-kit --skill fairvenue
```

Then ask your agent:

```text
Use the fairvenue skill to connect my strategy to Arena. Start with public market data and a
no-order dry run. I will provide the API URL and a local credential-file path, never a key in chat.
```

The skill explains setup, signed orders, private reads, book resynchronization and safe retries.
It does not install the Python SDK, create an account or submit orders by itself. The skill bundle
is self-contained; installing it does not require access to our private backend repository.

See the [skill instructions](skills/fairvenue/SKILL.md) or use the
[copy-paste integration prompt](docs/AI_AGENT_PROMPT.md) without installing a skill.

## Install the Python SDK

```bash
git clone https://github.com/FairVenue/fairvenue-agent-kit.git
cd fairvenue-agent-kit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The Python import and CLI remain `fairvenue`. This package is not published to PyPI; install from
this checkout, not an unrelated package with a similar name.

## Local account

Run the Arena backend on `127.0.0.1:8088`, then create an owner-only credential file:

```bash
fairvenue init-local \
  --base-url http://127.0.0.1:8088 \
  --credentials .fairvenue/testnet.json

fairvenue verify --credentials .fairvenue/testnet.json
```

`init-local` refuses remote hosts, refuses to overwrite an existing file, and writes credentials
with mode `0600`. It never prints the private key. Hosted testnet users create a separate bot agent
credential from Arena's **System** panel and must not call local reset/faucet endpoints.

## Read public market data

```python
from fairvenue import FairVenueClient

with FairVenueClient("http://127.0.0.1:8088") as client:
    print(client.info({"type": "bbo", "market_id": 0}))
```

## Submit a signed post-only order

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

## Examples

- `examples/public_market_data.py` — metadata and current BBO, no credentials.
- `examples/place_post_only.py` — safe post-only example; submission requires
  `FAIRVENUE_SUBMIT=1`.
- `examples/protected_ioc.py` — converts maximum slippage into an IOC protection price.
- `examples/stream_order_book.py` — public book stream; stops safely on a sequence gap.
- `docs/AI_AGENT_PROMPT.md` — a copy-paste prompt for a coding agent.

## Protocol invariants

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
