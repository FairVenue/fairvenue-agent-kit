# Connect without placing an order

## SDK and access

Clone `https://github.com/FairVenue/fairvenue-agent-kit` into an appropriate tools directory, record
the reviewed commit and install it in the project's Python 3.11+ virtual environment. For example,
from that checkout: `python -m pip install -e .`. No PyPI release is advertised.

For hosted access, give the user [Create an Arena API key](https://app.fairvenue.xyz/?setup=api-key)
and ask them to click **Create & download API key**. Use an operator-provided local/staging origin
instead when applicable. Older deployments keep **System → Create API key → Download JSON**.
The user signs in first if necessary. The browser generates the
key; the server receives only its public half. Move the file outside source control to an
owner-only location (`0600` on Linux/macOS). No private key belongs in the conversation.

For an already running isolated local backend only, explicit account-creation authorization
permits `fairvenue init-local --base-url http://127.0.0.1:8088 --credentials .fairvenue/testnet.json`.
It is not a hosted onboarding mechanism. The backend is not included with this skill or SDK.

Configuration:

- `FAIRVENUE_API_URL`: the approved API base URL, HTTPS except isolated loopback development.
- `FAIRVENUE_CREDENTIALS`: local credential-file path; never the key itself.
- Credential JSON contains `api_url`, `environment`, `account_id`, `agent_key_id`,
  `private_key_seed`. Read it through `Credentials.from_file`, never dump its contents.

For authenticated checks use `fairvenue verify --credentials /secure/path/arena-agent.json`.
Check settlement and market configuration separately as in the public read below. If an explicit
API URL and the credential's API URL disagree, stop and resolve the mismatch rather than copying
credentials to a new host.

## Public dry run

This needs no credentials and sends no orders:

```python
import os

from fairvenue import FairVenueClient

with FairVenueClient(os.environ["FAIRVENUE_API_URL"]) as client:
    meta = client.info({"type": "meta"})
    if (meta.get("api_version"), meta.get("environment"), meta.get("settlement")) != (
        "v1",
        "testnet",
        "virtual",
    ):
        raise RuntimeError("Unexpected API environment; stopping")
    market = next((m for m in meta["markets"] if m["market_id"] == 0), None)
    if market is None:
        raise RuntimeError("Expected simulator market is unavailable")
    bbo = client.info({"type": "bbo", "market_id": market["market_id"]})
    print({"dry_run": True, "market_id": market["market_id"], "bbo": bbo})
```

This checks connectivity only; a null/stale quote is not authorization to trade. Size/tick,
reference-feed health and strategy-specific risk checks must pass before enabling submission.

Further setup and examples: [SDK quickstart](https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/QUICKSTART.md).
