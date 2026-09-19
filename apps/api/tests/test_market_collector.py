import asyncio

import pytest

from app.integrations.bybit.schemas import BybitKlineData
from app.services.market_collector import MarketCollector
from app.services.market_universe import MarketUniverseUnavailable


def _kline(confirm: bool, close: str = "100.5") -> BybitKlineData:
    return BybitKlineData(
        start=1700000000000,
        end=1700000059999,
        interval="1",
        open="100.0",
        high="101.0",
        low="99.5",
        close=close,
        volume="10.0",
        turnover="1005.0",
        confirm=confirm,
        timestamp=1700000060000,
    )


class _FakeInstrument:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _FakeMarketUniverse:
    def __init__(self, symbols: list[str] | None = None, unavailable: bool = False) -> None:
        self._symbols = symbols or []
        self._unavailable = unavailable

    async def discover_universe(self):
        if self._unavailable:
            raise MarketUniverseUnavailable("bybit is down")
        return [_FakeInstrument(s) for s in self._symbols]


class _FakeClient:
    """Stands in for BybitKlineWebSocketClient: just waits for stop_event."""

    instances: list["_FakeClient"] = []

    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        _FakeClient.instances.append(self)

    async def run(self, stop_event: asyncio.Event) -> None:
        await stop_event.wait()


@pytest.fixture(autouse=True)
def _reset_fake_client_instances():
    _FakeClient.instances = []
    yield
    _FakeClient.instances = []


async def test_start_shards_universe_across_connections():
    universe = _FakeMarketUniverse(symbols=[f"SYM{i}USDT" for i in range(5)])
    collector = MarketCollector(
        universe,
        ws_base_url="wss://example.invalid",
        symbols_per_connection=2,
        client_factory=_FakeClient,
    )

    await collector.start()
    try:
        assert collector.status.symbols_subscribed == 5
        assert collector.status.connections_total == 3  # ceil(5 / 2)
        assert collector.status.running is True
        assert [c.symbols for c in _FakeClient.instances] == [
            ["SYM0USDT", "SYM1USDT"],
            ["SYM2USDT", "SYM3USDT"],
            ["SYM4USDT"],
        ]
    finally:
        await collector.stop()


async def test_start_with_empty_universe_creates_no_connections():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=[]),
        ws_base_url="wss://example.invalid",
        client_factory=_FakeClient,
    )

    await collector.start()
    try:
        assert collector.status.connections_total == 0
        assert collector.status.symbols_subscribed == 0
        assert collector.status.running is True
    finally:
        await collector.stop()


async def test_start_survives_universe_discovery_failure():
    collector = MarketCollector(
        _FakeMarketUniverse(unavailable=True),
        ws_base_url="wss://example.invalid",
        client_factory=_FakeClient,
    )

    await collector.start()  # must not raise
    try:
        assert collector.status.connections_total == 0
        assert collector.status.running is True
    finally:
        await collector.stop()


async def test_handle_kline_ignores_unconfirmed_candles():
    sunk: list = []
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT"]),
        ws_base_url="wss://example.invalid",
        sink=sunk.append,
        client_factory=_FakeClient,
    )

    await collector._handle_kline("BTCUSDT", _kline(confirm=False))

    assert sunk == []
    assert collector.status.candles_received == 0
    assert collector.status.last_candle_at is None


async def test_handle_kline_publishes_confirmed_candles_and_updates_status():
    sunk: list = []
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT"]),
        ws_base_url="wss://example.invalid",
        sink=sunk.append,
        client_factory=_FakeClient,
    )

    await collector._handle_kline("BTCUSDT", _kline(confirm=True, close="43005.5"))

    assert len(sunk) == 1
    candle = sunk[0]
    assert candle.exchange == "bybit"
    assert candle.symbol == "BTCUSDT"
    assert candle.timeframe == "1m"
    assert str(candle.close) == "43005.5"
    assert candle.key == (candle.exchange, candle.symbol, candle.timeframe, candle.open_time)

    assert collector.status.candles_received == 1
    assert collector.status.last_candle_at == candle.close_time


async def test_handle_kline_works_for_multiple_symbols_independently():
    sunk: list = []
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT", "ETHUSDT"]),
        ws_base_url="wss://example.invalid",
        sink=sunk.append,
        client_factory=_FakeClient,
    )

    await collector._handle_kline("BTCUSDT", _kline(confirm=True))
    await collector._handle_kline("ETHUSDT", _kline(confirm=True))

    assert {c.symbol for c in sunk} == {"BTCUSDT", "ETHUSDT"}
    assert collector.status.candles_received == 2


async def test_connection_state_changes_are_tracked_and_clamped_at_zero():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT"]),
        ws_base_url="wss://example.invalid",
        client_factory=_FakeClient,
    )

    collector._handle_connection_state_change(True)
    collector._handle_connection_state_change(True)
    assert collector.status.connections_active == 2

    collector._handle_connection_state_change(False)
    collector._handle_connection_state_change(False)
    collector._handle_connection_state_change(False)  # extra "down" must not go negative
    assert collector.status.connections_active == 0


async def test_add_symbols_starts_new_connections_without_disturbing_existing_ones():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT", "ETHUSDT"]),
        ws_base_url="wss://example.invalid",
        symbols_per_connection=2,
        client_factory=_FakeClient,
    )
    await collector.start()
    try:
        assert collector.status.connections_total == 1
        assert collector.status.symbols_subscribed == 2
        original_instances = list(_FakeClient.instances)

        await collector.add_symbols(["SOLUSDT", "AVAXUSDT"])

        assert collector.status.connections_total == 2
        assert collector.status.symbols_subscribed == 4
        # The original connection is untouched — this is purely additive.
        assert _FakeClient.instances[0] is original_instances[0]
        assert _FakeClient.instances[-1].symbols == ["SOLUSDT", "AVAXUSDT"]
    finally:
        await collector.stop()


async def test_add_symbols_before_start_is_a_noop():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=[]),
        ws_base_url="wss://example.invalid",
        client_factory=_FakeClient,
    )

    await collector.add_symbols(["BTCUSDT"])  # must not raise

    assert collector.status.connections_total == 0
    assert _FakeClient.instances == []


async def test_add_symbols_with_empty_list_is_a_noop():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=["BTCUSDT"]),
        ws_base_url="wss://example.invalid",
        client_factory=_FakeClient,
    )
    await collector.start()
    try:
        before = collector.status.connections_total
        await collector.add_symbols([])
        assert collector.status.connections_total == before
    finally:
        await collector.stop()


async def test_stop_cancels_all_connection_tasks_cleanly():
    collector = MarketCollector(
        _FakeMarketUniverse(symbols=[f"SYM{i}USDT" for i in range(3)]),
        ws_base_url="wss://example.invalid",
        symbols_per_connection=1,
        client_factory=_FakeClient,
    )

    await collector.start()
    assert collector.status.connections_total == 3

    await asyncio.wait_for(collector.stop(), timeout=1)

    assert collector.status.running is False
    assert collector.status.connections_active == 0
