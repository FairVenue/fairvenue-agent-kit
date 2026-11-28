"""Reconnect-capable FairVenue WebSocket event stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from secrets import SystemRandom
from typing import Any
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import connect, process_exception
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .client import FairVenueClient, JsonObject
from .errors import FairVenueApiError, FairVenueError

STABLE_CONNECTION_SECONDS = 30.0


def websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FairVenueError("base URL must be an absolute HTTP(S) URL")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/api/v1/ws", "", "", ""))


def _auth_refusal(reply: object) -> FairVenueApiError | None:
    if not isinstance(reply, dict) or reply.get("channel") != "error":
        return None
    data = reply.get("data")
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str) and isinstance(data, dict):
        code = data.get("code")
    return FairVenueApiError(
        code=code if isinstance(code, str) else "authentication_failed",
        message="private stream authentication was refused",
        status_code=401,
    )


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
        loop = asyncio.get_running_loop()
        backoff = self.initial_backoff
        refused_once = False
        while True:
            connected_at: float | None = None
            retry_after = 0.0
            try:
                async with connect(
                    websocket_url(self.client.base_url), max_size=64 * 1024
                ) as socket:
                    connected_at = loop.time()
                    if private:
                        token = await asyncio.to_thread(self.client.auth_token)
                        await socket.send(json.dumps({"method": "authenticate", "token": token}))
                        authenticated = json.loads(await socket.recv())
                        refusal = _auth_refusal(authenticated)
                        if refusal is not None:
                            if refused_once:
                                raise refusal
                            # A cached token can expire in flight: retry once with a fresh one.
                            refused_once = True
                            self.client.forget_auth_token()
                            raise FairVenueError("private stream token was refused")
                        if (
                            not isinstance(authenticated, dict)
                            or authenticated.get("channel") != "authenticated"
                        ):
                            raise FairVenueError("private stream authentication failed")
                        refused_once = False
                    for subscription in subscriptions:
                        await socket.send(
                            json.dumps({"method": "subscribe", "subscription": dict(subscription)})
                        )
                    async for raw in socket:
                        value = json.loads(raw)
                        if isinstance(value, dict):
                            yield value
            except asyncio.CancelledError:
                raise
            except FairVenueApiError as error:
                if error.status_code != 429 and error.status_code < 500:
                    raise
                retry_after = (error.retry_after_ms or 0) / 1000
            except ConnectionClosed:
                pass
            except WebSocketException as error:
                retryable_status = (
                    isinstance(error, InvalidStatus) and error.response.status_code == 429
                )
                fatal = None if retryable_status else process_exception(error)
                if fatal is not None:
                    raise fatal from None
            except (OSError, TimeoutError, ValueError, FairVenueError):
                pass
            if connected_at is not None and loop.time() - connected_at >= STABLE_CONNECTION_SECONDS:
                backoff = self.initial_backoff
            await asyncio.sleep(max(retry_after, backoff * SystemRandom().uniform(0.5, 1.0)))
            backoff = min(self.maximum_backoff, backoff * 2)
