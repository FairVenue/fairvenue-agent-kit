"""Read public Arena metadata and BBO without credentials."""

from __future__ import annotations

import os

from fairvenue import FairVenueClient

api_url = os.environ.get("FAIRVENUE_API_URL", "http://127.0.0.1:8088")

with FairVenueClient(api_url) as client:
    metadata = client.info({"type": "meta"})
    bbo = client.info({"type": "bbo", "market_id": 0})

print(
    {
        "environment": metadata["environment"],
        "settlement": metadata["settlement"],
        "market": metadata["markets"][0],
        "bbo": bbo,
    }
)
