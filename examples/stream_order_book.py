"""Consume the public book and force a fresh snapshot after any sequence gap."""

from __future__ import annotations

import asyncio
import os
from contextlib import aclosing

from fairvenue import FairVenueClient, FairVenueStream


async def follow_book(stream: FairVenueStream) -> None:
    previous_sequence: int | None = None
    events = stream.events([{"type": "l2_book", "market_id": 0, "depth": 50}])
    async with aclosing(events):
        async for event in events:
            if event.get("channel") != "l2_book":
                continue
            data = event.get("data", {})
            if data.get("type") == "snapshot":
                previous_sequence = int(data["data"]["sequence"])
                print({"book": "snapshot", "sequence": previous_sequence})
                continue
            delta = data.get("data", {})
            start = int(delta["start_sequence"])
            if previous_sequence is not None and int(delta["end_sequence"]) <= previous_sequence:
                continue
            if previous_sequence is None or start != previous_sequence + 1:
                print("Book sequence gap. Reconnecting for a clean snapshot.")
                return
            previous_sequence = int(delta["end_sequence"])
            print({"book": "delta", "sequence": previous_sequence})


async def main() -> None:
    api_url = os.environ.get("FAIRVENUE_API_URL", "http://127.0.0.1:8088")
    with FairVenueClient(api_url) as client:
        stream = FairVenueStream(client)
        while True:
            await follow_book(stream)
            await asyncio.sleep(1.0)


asyncio.run(main())
