"""Consume the public book and force a fresh snapshot after any sequence gap."""

from __future__ import annotations

import asyncio
import os

from fairvenue import FairVenueClient, FairVenueStream


async def main() -> None:
    api_url = os.environ.get("FAIRVENUE_API_URL", "http://127.0.0.1:8088")
    with FairVenueClient(api_url) as client:
        stream = FairVenueStream(client)
        previous_sequence: int | None = None
        async for event in stream.events([{"type": "l2_book", "market_id": 0, "depth": 50}]):
            if event.get("channel") != "l2_book":
                continue
            data = event.get("data", {})
            if data.get("type") == "snapshot":
                previous_sequence = int(data["data"]["sequence"])
                print({"book": "snapshot", "sequence": previous_sequence})
                continue
            delta = data.get("data", {})
            start = int(delta["start_sequence"])
            if previous_sequence is None or start != previous_sequence + 1:
                print("Book sequence gap. Reconnecting for a clean snapshot.")
                return
            previous_sequence = int(delta["end_sequence"])
            print({"book": "delta", "sequence": previous_sequence})


asyncio.run(main())
