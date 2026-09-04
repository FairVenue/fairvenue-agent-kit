# API setup

## 1. Install

```bash
git clone https://github.com/FairVenue/fairvenue-agent-kit.git
cd fairvenue-agent-kit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 2. Start the local Arena backend

Only if you already have an authorized local backend checkout. The backend is not bundled in
this repository. Otherwise use an operator-provided Arena URL and the hosted access steps below;
do not assume a public testnet hostname is available.

From your backend checkout:

```bash
cd arena-backend
FAIRVENUE_ENGINE_REFERENCE_FEED=0 \
FAIRVENUE_ENGINE_MOCK_REFERENCE_DATA=1 \
FAIRVENUE_ENGINE_LOCAL_BROWSER=1 \
FAIRVENUE_ENGINE_EVENT_LOG=/tmp/fairvenue-arena/events.sqlite3 \
./scripts/cargo-local.sh run
```

## 3. Create local credentials

```bash
fairvenue init-local \
  --base-url http://127.0.0.1:8088 \
  --credentials .fairvenue/testnet.json
```

This local-only operation creates a virtual account, funds it with virtual USDC and writes a new
Ed25519 seed to an owner-only file. It refuses remote URLs and existing destinations.

## 4. Verify

```bash
fairvenue verify --credentials .fairvenue/testnet.json
FAIRVENUE_API_URL=http://127.0.0.1:8088 python examples/public_market_data.py
```

## 5. Run an order example

The example defaults to dry run:

```bash
FAIRVENUE_CREDENTIALS=.fairvenue/testnet.json python examples/place_post_only.py
```

To submit the simulated order deliberately:

```bash
FAIRVENUE_SUBMIT=1 \
FAIRVENUE_CREDENTIALS=.fairvenue/testnet.json \
python examples/place_post_only.py
```

The returned `accepted` response is only a durable receipt. The example waits for the final
transaction projection before reporting the result.

## Hosted access

Use the Arena URL provided with your access. Do not call `init-local`, reset or faucet against a
hosted testnet. Sign in to Arena, open
**System**, choose **Create API key**, and download the JSON once. FairVenue stores only the
public key. Move the file to an encrypted owner-only location, apply `chmod 600 <file>` on Linux or
macOS, and run `fairvenue verify` before starting a strategy.

```bash
fairvenue verify --credentials /secure/path/arena-agent.json
```

`FAIRVENUE_CREDENTIALS` is a file path, not a private key. `.env.example` documents the environment
variables; the examples read the environment and do not automatically load a `.env` file. For
public examples, set `FAIRVENUE_API_URL` to your operator-provided base URL.
