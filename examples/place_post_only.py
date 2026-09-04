"""Prepare, and only with explicit opt-in submit, one simulated post-only order."""

from __future__ import annotations

import os
from decimal import Decimal

from fairvenue import Credentials, FairVenueClient

path = os.environ.get("FAIRVENUE_CREDENTIALS", ".fairvenue/testnet.json")
credentials = Credentials.from_file(path)

with FairVenueClient.from_credentials(credentials) as client:
    bbo = client.info({"type": "bbo", "market_id": 0})
    bid = bbo.get("bid")
    if not isinstance(bid, str):
        raise SystemExit("No bid is available")
    price = Decimal(bid)
    order = {
        "market_id": 0,
        "side": "buy",
        "price": str(price),
        "size": "0.001",
        "time_in_force": "Alo",
        "reduce_only": False,
        "client_order_id": client.new_client_order_id(),
    }
    print({"prepared_order": order, "settlement": "virtual"})
    if os.environ.get("FAIRVENUE_SUBMIT") != "1":
        raise SystemExit("Dry run only. Set FAIRVENUE_SUBMIT=1 to submit this simulated order.")
    accepted = client.submit({"type": "order", "orders": [order]})
    final = client.wait_for_transaction(accepted["response"]["tx_hash"])
    print({"accepted": accepted["response"], "transaction": final})
