"""Synchronous FairVenue Arena API v1 client."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any, Literal, cast

import httpx

from .credentials import Credentials
from .errors import FairVenueApiError, FairVenueError
from .nonce import NonceManager
from .signing import auth_message, sign, signing_message

JsonObject = dict[str, Any]
Side = Literal["buy", "sell"]
TimeInForce = Literal["Alo", "Ioc", "Gtc"]

_PRIVATE_INFO = {
    "account_state",
    "open_orders",
    "order_status",
    "historical_orders",
    "fills",
    "positions",
    "funding_payments",
    "transaction_status",
}


def _normalize_exact_json(value: Any, path: str = "request") -> Any:
    if isinstance(value, float):
        raise FairVenueError(f"{path} contains a float; use a decimal string")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise FairVenueError(f"{path} contains a non-string object key")
            normalized[key] = _normalize_exact_json(child, f"{path}.{key}")
        return normalized
    if isinstance(value, list):
        return [
            _normalize_exact_json(child, f"{path}[{index}]") for index, child in enumerate(value)
        ]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise FairVenueError(f"{path} contains an unsupported value")


class FairVenueClient:
    """Small API v1 client that keeps signing and strategy state separate."""

    def __init__(
        self,
        base_url: str,
        *,
        credentials: Credentials | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self._nonces = NonceManager()
        self._token: str | None = None
        self._token_expires_at = 0

    @classmethod
    def from_credentials(
        cls,
        credentials: Credentials,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> FairVenueClient:
        return cls(
            credentials.api_url,
            credentials=credentials,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> FairVenueClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @staticmethod
    def new_client_order_id() -> str:
        return f"0x{secrets.token_hex(16)}"

    def _post(self, path: str, payload: Mapping[str, Any], token: str | None = None) -> JsonObject:
        normalized = _normalize_exact_json(payload)
        headers = {"Authorization": f"Bearer {token}"} if token else None
        response = self._http.post(path, json=normalized, headers=headers)
        if response.is_error:
            try:
                body = response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
            except ValueError:
                error = {}
            raise FairVenueApiError(
                code=str(error.get("code", "http_error")),
                message=str(error.get("message", "request failed")),
                status_code=response.status_code,
                retry_after_ms=(
                    int(error["retry_after_ms"])
                    if isinstance(error.get("retry_after_ms"), int)
                    else None
                ),
            )
        value = response.json()
        if not isinstance(value, dict):
            raise FairVenueError("API response must be a JSON object")
        return cast(JsonObject, value)

    def info(self, request: Mapping[str, Any]) -> JsonObject:
        request_type = request.get("type")
        token = self.auth_token() if request_type in _PRIVATE_INFO else None
        return self._post("/api/v1/info", request, token)

    def auth_token(self) -> str:
        credentials = self._require_credentials()
        now = time.time_ns() // 1_000_000
        if self._token is not None and now + 1_000 < self._token_expires_at:
            return self._token
        challenge = self._post(
            "/api/v1/auth",
            {
                "type": "challenge",
                "account_id": credentials.account_id,
                "agent_key_id": credentials.agent_key_id,
            },
        )
        challenge_value = str(challenge["challenge"])
        expires_at = int(challenge["expires_at"])
        signature = sign(
            credentials.private_key_seed,
            auth_message(
                environment=credentials.environment,
                account_id=credentials.account_id,
                agent_key_id=credentials.agent_key_id,
                challenge=challenge_value,
                expires_at=expires_at,
            ),
        )
        verified = self._post(
            "/api/v1/auth",
            {
                "type": "verify",
                "account_id": credentials.account_id,
                "agent_key_id": credentials.agent_key_id,
                "challenge": challenge_value,
                "signature": signature,
            },
        )
        self._token = str(verified["token"])
        self._token_expires_at = int(verified["expires_at"])
        return self._token

    def submit(self, action: Mapping[str, Any], *, expires_in_ms: int = 5_000) -> JsonObject:
        credentials = self._require_credentials()
        if not 1_000 <= expires_in_ms <= 30_000:
            raise FairVenueError("expires_in_ms must be between 1000 and 30000")
        normalized_action = _normalize_exact_json(action, "action")
        if not isinstance(normalized_action, dict):
            raise FairVenueError("action must be a JSON object")
        nonce = self._nonces.next()
        expires_after = time.time_ns() // 1_000_000 + expires_in_ms
        message = signing_message(
            environment=credentials.environment,
            account_id=credentials.account_id,
            agent_key_id=credentials.agent_key_id,
            nonce=nonce,
            expires_after=expires_after,
            action=normalized_action,
        )
        return self._post(
            "/api/v1/exchange",
            {
                "account_id": credentials.account_id,
                "agent_key_id": credentials.agent_key_id,
                "nonce": nonce,
                "expires_after": expires_after,
                "action": normalized_action,
                "signature": sign(credentials.private_key_seed, message),
            },
        )

    def place_limit(
        self,
        *,
        market_id: int,
        side: Side,
        price: str | Decimal,
        size: str | Decimal,
        time_in_force: TimeInForce = "Alo",
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> JsonObject:
        order = {
            "market_id": market_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "time_in_force": time_in_force,
            "reduce_only": reduce_only,
            "client_order_id": client_order_id,
        }
        return self.submit({"type": "order", "orders": [order]})

    def protected_ioc(
        self,
        *,
        market_id: int,
        side: Side,
        size: str | Decimal,
        max_slippage_bps: str | Decimal,
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> JsonObject:
        bbo = self.info({"type": "bbo", "market_id": market_id})
        reference = bbo.get("ask" if side == "buy" else "bid")
        if not isinstance(reference, str):
            raise FairVenueError("BBO side is unavailable")
        reference_price = Decimal(reference)
        fraction = Decimal(str(max_slippage_bps)) / Decimal(10_000)
        if not Decimal(0) <= fraction <= Decimal("0.1"):
            raise FairVenueError("max_slippage_bps must be between 0 and 1000")
        raw_protection = reference_price * (
            Decimal(1) + fraction if side == "buy" else Decimal(1) - fraction
        )
        meta = self.info({"type": "meta"})
        markets = meta.get("markets")
        market = (
            next(
                (
                    candidate
                    for candidate in markets
                    if isinstance(candidate, dict) and candidate.get("market_id") == market_id
                ),
                None,
            )
            if isinstance(markets, list)
            else None
        )
        if not isinstance(market, dict) or not isinstance(market.get("tick_size"), str):
            raise FairVenueError("market tick size is unavailable")
        tick = Decimal(market["tick_size"])
        rounding = ROUND_DOWN if side == "buy" else ROUND_UP
        protection = (raw_protection / tick).to_integral_value(rounding=rounding) * tick
        return self.place_limit(
            market_id=market_id,
            side=side,
            price=protection,
            size=size,
            time_in_force="Ioc",
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )

    def wait_for_transaction(
        self,
        tx_hash: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.05,
    ) -> JsonObject:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.info({"type": "transaction_status", "tx_hash": tx_hash})
            transaction = response.get("transaction")
            if isinstance(transaction, dict) and transaction.get("status") in {
                "applied",
                "rejected",
            }:
                return cast(JsonObject, transaction)
            time.sleep(poll_interval)
        raise TimeoutError("transaction did not reach a final state before the timeout")

    def _require_credentials(self) -> Credentials:
        if self.credentials is None:
            raise FairVenueError("this operation requires agent credentials")
        return self.credentials
