import asyncio
import json
from typing import Any

import fairvenue.stream as stream_module
from fairvenue.stream import FairVenueStream, websocket_url


def test_websocket_url_tracks_transport_security() -> None:
    assert websocket_url("http://127.0.0.1:8088") == "ws://127.0.0.1:8088/api/v1/ws"
    assert websocket_url("https://testnet.example/base") == "wss://testnet.example/api/v1/ws"


def test_private_stream_authenticates_before_subscribing(monkeypatch: Any) -> None:
    class Client:
        base_url = "https://testnet.example"

        @staticmethod
        def auth_token() -> str:
            return "opaque-read-token"

    class Socket:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.events = iter(
                [json.dumps({"channel": "subscribed", "data": {"type": "order_updates"}})]
            )

        async def send(self, raw: str) -> None:
            self.sent.append(json.loads(raw))

        async def recv(self) -> str:
            return json.dumps({"channel": "authenticated", "data": {"account_id": "acc_test"}})

        def __aiter__(self) -> "Socket":
            return self

        async def __anext__(self) -> str:
            try:
                return next(self.events)
            except StopIteration as error:
                raise StopAsyncIteration from error

    class Connection:
        def __init__(self, socket: Socket) -> None:
            self.socket = socket

        async def __aenter__(self) -> Socket:
            return self.socket

        async def __aexit__(self, *_: object) -> None:
            return None

    socket = Socket()
    connect_calls: list[tuple[str, int]] = []

    def connect(url: str, *, max_size: int) -> Connection:
        connect_calls.append((url, max_size))
        return Connection(socket)

    monkeypatch.setattr(stream_module, "connect", connect)

    async def receive_one() -> dict[str, object]:
        events = FairVenueStream(Client()).events(  # type: ignore[arg-type]
            [{"type": "order_updates"}], private=True
        )
        event = await anext(events)
        await events.aclose()
        return event

    assert asyncio.run(receive_one()) == {
        "channel": "subscribed",
        "data": {"type": "order_updates"},
    }
    assert connect_calls == [("wss://testnet.example/api/v1/ws", 64 * 1024)]
    assert socket.sent == [
        {"method": "authenticate", "token": "opaque-read-token"},
        {"method": "subscribe", "subscription": {"type": "order_updates"}},
    ]
