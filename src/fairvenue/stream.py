"""Reconnect-capable FairVenue WebSocket event stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import connect

from .client import FairVenueClient, JsonObject
from .errors import FairVenueError


def websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FairVenueError("base URL must be an absolute HTTP(S) URL")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/api/v1/ws", "", "", ""))


class FairVenueStream:
    """Reconnect and resubscribe while leaving state reconciliation to the consumer."""

    def __init__(
        self,
        client: FairVenueClient,
        *,
        initial_backoff: float = 0.25,
        maximum_backoff: float = 5.0,
    ) -> None:
        self.client = client
        self.initial_backoff = initial_backoff
        self.maximum_backoff = maximum_backoff

    async def events(
        self,
        subscriptions: Sequence[Mapping[str, Any]],
        *,
        private: bool = False,
    ) -> AsyncIterator[JsonObject]:
        backoff = self.initial_backoff
        while True:
            try:
                async with connect(
                    websocket_url(self.client.base_url), max_size=64 * 1024
                ) as socket:
                    if private:
                        token = await asyncio.to_thread(self.client.auth_token)
                        await socket.send(json.dumps({"method": "authenticate", "token": token}))
                        authenticated = json.loads(await socket.recv())
                        if authenticated.get("channel") != "authenticated":
                            raise FairVenueError("private stream authentication failed")
                    for subscription in subscriptions:
                        await socket.send(
                            json.dumps({"method": "subscribe", "subscription": dict(subscription)})
                        )
                    backoff = self.initial_backoff
                    async for raw in socket:
                        value = json.loads(raw)
                        if isinstance(value, dict):
                            yield value
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError, ValueError, FairVenueError):
                await asyncio.sleep(backoff)
                backoff = min(self.maximum_backoff, backoff * 2)
