"""Submit a simulated IOC with an explicit maximum-slippage protection price."""

from __future__ import annotations

import os

from fairvenue import Credentials, FairVenueClient

if os.environ.get("FAIRVENUE_SUBMIT") != "1":
    raise SystemExit("Set FAIRVENUE_SUBMIT=1 to submit the simulated IOC example.")

credentials = Credentials.from_file(
    os.environ.get("FAIRVENUE_CREDENTIALS", ".fairvenue/testnet.json")
)

with FairVenueClient.from_credentials(credentials) as client:
    accepted = client.protected_ioc(
        market_id=0,
        side="buy",
        size="0.001",
        max_slippage_bps="5",
        client_order_id=client.new_client_order_id(),
    )
    final = client.wait_for_transaction(accepted["response"]["tx_hash"])
    print({"accepted": accepted["response"], "transaction": final})
