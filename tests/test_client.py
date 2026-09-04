from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from fairvenue import Credentials, FairVenueClient, FairVenueError


def credentials() -> Credentials:
    return Credentials(
        api_url="https://testnet.example",
        environment="testnet",
        account_id="acc_example1",
        agent_key_id="agent_example1",
        private_key_seed="07" * 32,
    )


def test_rejects_float_money_before_transport() -> None:
    client = FairVenueClient.from_credentials(
        credentials(),
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    with client, pytest.raises(FairVenueError, match="contains a float"):
        client.submit(
            {
                "type": "order",
                "orders": [
                    {
                        "market_id": 0,
                        "side": "buy",
                        "price": 100.5,
                        "size": "0.001",
                        "time_in_force": "Alo",
                    }
                ],
            }
        )


@pytest.mark.parametrize("field", ["price", "size", "max_slippage_bps"])
def test_helpers_do_not_convert_float_inputs_before_validation(field: str) -> None:
    def transport(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Invalid money must fail before any request")

    with (
        FairVenueClient.from_credentials(
            credentials(), transport=httpx.MockTransport(transport)
        ) as client,
        pytest.raises(FairVenueError, match="contains a float"),
    ):
        if field == "max_slippage_bps":
            client.protected_ioc(market_id=0, side="buy", size="0.001", max_slippage_bps=0.5)
        else:
            values = {"price": "100.1", "size": "0.001", field: 0.5}
            client.place_limit(market_id=0, side="buy", **values)


def test_authenticates_private_read_and_never_sends_seed() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["type"] == "challenge":
            return httpx.Response(200, json={"challenge": "0xabc", "expires_at": 9999999999999})
        if body["type"] == "verify":
            return httpx.Response(200, json={"token": "opaque", "expires_at": 9999999999999})
        assert request.headers["Authorization"] == "Bearer opaque"
        return httpx.Response(200, json={"orders": []})

    with FairVenueClient.from_credentials(
        credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        assert client.info({"type": "open_orders"}) == {"orders": []}

    serialized = json.dumps(requests)
    assert credentials().private_key_seed not in serialized
    assert [request["type"] for request in requests] == ["challenge", "verify", "open_orders"]


def test_protected_ioc_uses_bbo_tick_and_decimal_strings() -> None:
    submitted_action: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submitted_action
        body = json.loads(request.content)
        if request.url.path == "/api/v1/info" and body["type"] == "bbo":
            return httpx.Response(200, json={"market_id": 0, "bid": "99.9", "ask": "100.1"})
        if request.url.path == "/api/v1/info" and body["type"] == "meta":
            return httpx.Response(200, json={"markets": [{"market_id": 0, "tick_size": "0.1"}]})
        if request.url.path == "/api/v1/exchange":
            submitted_action = body["action"]
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "response": {
                        "type": "accepted",
                        "tx_hash": f"0x{'01' * 32}",
                        "sequence": 1,
                        "accepted_at": 1,
                    },
                },
            )
        raise AssertionError(f"unexpected request {request.url.path} {body}")

    with FairVenueClient.from_credentials(
        credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        client.protected_ioc(
            market_id=0,
            side="buy",
            size=Decimal("0.001"),
            max_slippage_bps=Decimal("5"),
            client_order_id="0x1234567890abcdef1234567890abcdef",
        )

    order = submitted_action["orders"][0]  # type: ignore[index]
    assert order["price"] == "100.1"
    assert order["size"] == "0.001"
    assert order["time_in_force"] == "Ioc"
