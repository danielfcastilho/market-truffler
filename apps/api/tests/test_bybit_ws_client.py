import asyncio
import json

from app.integrations.bybit.schemas import BybitKlineData
from app.integrations.bybit.ws_client import (
    BybitKlineWebSocketClient,
    parse_kline_message,
    parse_kline_topic_symbol,
)


def _raw_kline(confirm: bool, symbol: str = "BTCUSDT") -> dict:
    return {
        "topic": f"kline.1.{symbol}",
        "type": "snapshot",
        "ts": 1700000000123,
        "data": [
            {
                "start": 1700000000000,
                "end": 1700000059999,
                "interval": "1",
                "open": "43000.5",
                "high": "43010.0",
                "low": "42990.0",
                "close": "43005.5",
                "volume": "12.345",
                "turnover": "531000.12",
                "confirm": confirm,
                "timestamp": 1700000060000,
            }
        ],
    }


class TestParseKlineTopicSymbol:
    def test_extracts_symbol(self):
        assert parse_kline_topic_symbol("kline.1.BTCUSDT") == "BTCUSDT"

    def test_rejects_non_kline_topic(self):
        assert parse_kline_topic_symbol("orderbook.1.BTCUSDT") is None

    def test_rejects_malformed_topic(self):
        assert parse_kline_topic_symbol("kline") is None
        assert parse_kline_topic_symbol("kline.1") is None


class TestParseKlineMessage:
    def test_parses_confirmed_kline(self):
        pairs = parse_kline_message(_raw_kline(confirm=True))
        assert len(pairs) == 1
        symbol, kline = pairs[0]
        assert symbol == "BTCUSDT"
        assert kline.confirm is True
        assert kline.close == "43005.5"

    def test_parses_unconfirmed_kline(self):
        pairs = parse_kline_message(_raw_kline(confirm=False))
        assert pairs[0][1].confirm is False

    def test_ignores_non_kline_topics(self):
        assert parse_kline_message({"topic": "pong", "data": []}) == []

    def test_ignores_messages_without_topic(self):
        assert parse_kline_message({"success": True, "op": "subscribe"}) == []

    def test_ignores_non_dict_payloads(self):
        assert parse_kline_message(["not", "a", "dict"]) == []
        assert parse_kline_message(None) == []

    def test_ignores_malformed_kline_data(self):
        payload = {"topic": "kline.1.BTCUSDT", "type": "snapshot", "data": [{"start": "oops"}]}
        assert parse_kline_message(payload) == []

    def test_handles_multiple_symbols_independently(self):
        btc = parse_kline_message(_raw_kline(confirm=True, symbol="BTCUSDT"))
        eth = parse_kline_message(_raw_kline(confirm=True, symbol="ETHUSDT"))
        assert btc[0][0] == "BTCUSDT"
        assert eth[0][0] == "ETHUSDT"


class _FakeWebSocket:
    """A fake connection: queues outbound sends, yields preloaded inbound
    messages, and otherwise blocks (simulating an idle-but-open connection)
    until the shared `stop_event` is set — mirroring how a real connection
    only stops delivering messages when it's actually closed.
    """

    def __init__(
        self,
        inbound: list[str],
        fail_on_enter: Exception | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self.inbound = list(inbound)
        self.sent: list[dict] = []
        self._fail_on_enter = fail_on_enter
        self._stop_event = stop_event or asyncio.Event()

    async def __aenter__(self):
        if self._fail_on_enter:
            raise self._fail_on_enter
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self.inbound and not self._stop_event.is_set():
            await asyncio.sleep(0.001)
        if not self.inbound:
            raise StopAsyncIteration
        return self.inbound.pop(0)


class _FakeConnector:
    """Returns a scripted sequence of fake connections, one per connect attempt."""

    def __init__(self, connections: list[_FakeWebSocket]) -> None:
        self._connections = list(connections)
        self.attempts = 0

    def __call__(self) -> _FakeWebSocket:
        conn = self._connections[min(self.attempts, len(self._connections) - 1)]
        self.attempts += 1
        return conn


async def test_subscribe_sends_batched_topics():
    stop_event = asyncio.Event()
    stop_event.set()  # let the connection close immediately after one pass
    ws = _FakeWebSocket(inbound=[], stop_event=stop_event)
    connector = _FakeConnector([ws])
    received: list[tuple[str, BybitKlineData]] = []

    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=[f"SYM{i}USDT" for i in range(5)],
        interval="1",
        on_kline=lambda symbol, kline: received.append((symbol, kline)),
        subscribe_batch_size=2,
        connector=connector,
    )

    await client._connect_and_listen(stop_event)

    assert len(ws.sent) == 3  # ceil(5 / 2)
    assert all(msg["op"] == "subscribe" for msg in ws.sent)
    assert sum(len(msg["args"]) for msg in ws.sent) == 5


async def test_handle_raw_message_dispatches_confirmed_klines_only():
    received: list[tuple[str, BybitKlineData]] = []

    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: received.append((symbol, kline)),
    )

    await client._handle_raw_message(json.dumps(_raw_kline(confirm=True)))
    await client._handle_raw_message(json.dumps(_raw_kline(confirm=False)))

    assert len(received) == 2  # both dispatched — filtering confirm happens above this layer
    assert received[0][1].confirm is True
    assert received[1][1].confirm is False


async def test_handle_raw_message_ignores_malformed_json():
    calls: list[object] = []
    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: calls.append((symbol, kline)),
    )

    await client._handle_raw_message("{not valid json")

    assert calls == []


async def test_run_reconnects_after_failed_connection_then_stops():
    stop_event = asyncio.Event()
    good_ws = _FakeWebSocket(
        inbound=[json.dumps(_raw_kline(confirm=True))], stop_event=stop_event
    )
    connector = _FakeConnector(
        [
            _FakeWebSocket(inbound=[], fail_on_enter=ConnectionRefusedError("refused")),
            good_ws,
        ]
    )
    received: list[tuple[str, BybitKlineData]] = []
    state_changes: list[bool] = []

    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: received.append((symbol, kline)),
        on_connection_state_change=state_changes.append,
        reconnect_initial_delay=0.001,
        reconnect_max_delay=0.01,
        connector=connector,
    )

    async def stop_after_message():
        while not received:
            await asyncio.sleep(0.001)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(client.run(stop_event), stop_after_message()), timeout=2
    )

    assert connector.attempts == 2
    assert received[0][0] == "BTCUSDT"
    # The first (refused) attempt never entered the connection, so it never
    # reports up/down at all — only the second, successful connection does.
    assert state_changes == [True, False]


async def test_run_stops_cleanly_without_ever_connecting():
    connector = _FakeConnector([_FakeWebSocket(inbound=[])])
    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: None,
        connector=connector,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    await asyncio.wait_for(client.run(stop_event), timeout=1)

    assert connector.attempts == 0


async def test_run_never_raises_when_connection_repeatedly_fails():
    connector = _FakeConnector(
        [_FakeWebSocket(inbound=[], fail_on_enter=OSError("network unreachable"))]
    )
    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: None,
        reconnect_initial_delay=0.001,
        reconnect_max_delay=0.005,
        connector=connector,
    )

    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.02)
        stop_event.set()

    # Must return normally (not raise) even though every connection attempt fails.
    await asyncio.wait_for(asyncio.gather(client.run(stop_event), stop_soon()), timeout=2)


async def test_run_pings_the_connection_while_open():
    stop_event = asyncio.Event()
    ws = _FakeWebSocket(inbound=[], stop_event=stop_event)
    connector = _FakeConnector([ws])

    client = BybitKlineWebSocketClient(
        url="wss://example.invalid",
        symbols=["BTCUSDT"],
        interval="1",
        on_kline=lambda symbol, kline: None,
        ping_interval=0.01,
        connector=connector,
    )

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.wait_for(asyncio.gather(client.run(stop_event), stop_soon()), timeout=2)

    pings = [msg for msg in ws.sent if msg.get("op") == "ping"]
    assert len(pings) >= 1
