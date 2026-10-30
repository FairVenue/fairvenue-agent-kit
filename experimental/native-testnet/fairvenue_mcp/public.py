"""Bounded public ``/api/v1/info`` reads for native TESTNET market data.

No credentials, cookies or redirects are ever sent. Replies are size-capped and
parsed without binary floats. Reads are cached and paced because the server's
public market lane (about 600 reads a minute) is shared by every participant.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import ssl
import time
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from . import __version__

InfoKind = Literal[
    "native_market", "native_book_history", "native_mark_history", "native_capabilities"
]

MAX_REPLY_BYTES = 262_144
#: Minimum spacing between two network reads from this process.
MIN_SPACING_SECONDS = 0.5


class PublicApiError(Exception):
    """A content-free public read failure with a stable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_constant(value: str) -> Any:
    raise ValueError(value)


class PublicApi:
    """Public reads against the approved HTTPS origin from the installed pins."""

    def __init__(self, api_url: str, settlement: str, *, timeout: float = 10.0) -> None:
        url = urlsplit(api_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.path != "/"
            or url.query
            or url.fragment
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("public API origin must be an approved https origin ending in /")
        self._host = url.hostname
        self._port = url.port
        self._settlement = settlement
        self._timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def info(self, kind: InfoKind, *, max_age: float = 0.0) -> dict[str, Any]:
        """One validated JSON object, reused while younger than ``max_age`` seconds.

        A network failure is retried once: these reads are idempotent. A rate
        limit is never retried here, because the lane is shared.
        """
        # History replies are large and computed on demand, so they get more time.
        timeout = self._timeout if kind in ("native_market", "native_capabilities") else 25.0
        async with self._lock:
            cached = self._cache.get(kind)
            if cached is not None and time.monotonic() - cached[0] <= max_age:
                return cached[1]
            for attempt in (1, 2):
                wait = self._last_request + MIN_SPACING_SECONDS * attempt - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request = time.monotonic()
                try:
                    value = await asyncio.wait_for(
                        asyncio.to_thread(self._request, kind, timeout), timeout + 5
                    )
                except TimeoutError:
                    error = PublicApiError(
                        "deadline", "The public TESTNET API did not answer in time."
                    )
                except PublicApiError as problem:
                    error = problem
                else:
                    self._cache[kind] = (time.monotonic(), value)
                    return value
                if error.code not in ("unavailable", "deadline"):
                    break
            raise error

    def _request(self, kind: str, timeout: float) -> dict[str, Any]:
        body = json.dumps({"type": kind}, separators=(",", ":")).encode()
        connection = http.client.HTTPSConnection(
            self._host, self._port, timeout=timeout, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "POST",
                "/api/v1/info",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "Connection": "close",
                    "User-Agent": f"fairvenue-mcp/{__version__}",
                    "X-FairVenue-Testnet-Deployment": self._settlement,
                },
            )
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader("Content-Type", "")
            data = response.read(MAX_REPLY_BYTES + 1)
        except (OSError, http.client.HTTPException):
            raise PublicApiError(
                "unavailable", "The public TESTNET API is unreachable (network or TLS)."
            ) from None
        finally:
            connection.close()
        if status == 429:
            raise PublicApiError(
                "rate_limited",
                "Public market reads are rate limited. The lane is shared by all participants, "
                "so wait a few seconds before reading again.",
            )
        if status != 200 or content_type.split(";", 1)[0].strip() != "application/json":
            raise PublicApiError("unavailable", f"The public TESTNET API answered HTTP {status}.")
        if len(data) > MAX_REPLY_BYTES:
            raise PublicApiError("response_invalid", "The public reply was larger than expected.")
        try:
            value = json.loads(
                data.decode("utf-8"), parse_float=Decimal, parse_constant=_reject_constant
            )
        except ValueError:
            raise PublicApiError(
                "response_invalid", "The public reply was not valid JSON."
            ) from None
        if not isinstance(value, dict):
            raise PublicApiError("response_invalid", "The public reply was not a JSON object.")
        return value
