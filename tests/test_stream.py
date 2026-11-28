import asyncio
import json
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedError

import fairvenue.stream as stream_module
from fairvenue.errors import FairVenueApiError
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


def test_stream_backs_off_after_every_disconnect_and_surfaces_auth_refusal(
    monkeypatch: Any,
) -> None:
    """Every close waits, a single frame does not reset the wait, a token refused twice stops."""

    class Client:
        base_url = "https://testnet.example"
        refused = False
        forgotten = 0

        @staticmethod
        def auth_token() -> str:
            return "opaque-read-token"

        def forget_auth_token(self) -> None:
            self.forgotten += 1

    client = Client()

    class Socket:
        def __init__(self, events: list[str], close: BaseException | None) -> None:
            self.events, self.close = iter(events), close

        async def send(self, raw: str) -> None:
            return None

        async def recv(self) -> str:
            if client.refused:
                return json.dumps({"channel": "error", "data": {"error": {"code": "unauthorized"}}})
            return json.dumps({"channel": "authenticated"})

        def __aiter__(self) -> "Socket":
            return self

        async def __anext__(self) -> str:
            try:
                return next(self.events)
            except StopIteration:
                if self.close is not None:
                    raise self.close from None
                raise StopAsyncIteration from None

    class Connection:
        def __init__(self, socket: Socket) -> None:
            self.socket = socket

        async def __aenter__(self) -> Socket:
            return self.socket

        async def __aexit__(self, *_: object) -> None:
            return None

    event = json.dumps({"channel": "l2_book"})
    sockets = [
        Socket([], None),
        Socket([], ConnectionClosedError(None, None)),
        Socket([event], None),
    ]
    connects: list[int] = []

    def connect(url: str, *, max_size: int) -> Connection:
        connects.append(len(connects))
        return Connection(sockets[min(len(connects) - 1, len(sockets) - 1)])

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(stream_module, "connect", connect)
    monkeypatch.setattr(stream_module.asyncio, "sleep", sleep)

    async def run() -> None:
        events = FairVenueStream(client, initial_backoff=1.0).events(  # type: ignore[arg-type]
            [{"type": "l2_book"}], private=True
        )
        assert await anext(events) == {"channel": "l2_book"}
        client.refused = True
        with pytest.raises(FairVenueApiError, match="unauthorized"):
            await anext(events)

    asyncio.run(run())
    assert client.forgotten == 1
    assert len(sleeps) == 4
    assert 0.5 <= sleeps[0] <= 1.0
    assert 1.0 <= sleeps[1] <= 2.0
    assert 2.0 <= sleeps[2] <= 4.0
    assert 2.5 <= sleeps[3] <= 5.0
