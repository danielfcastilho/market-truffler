"""Continuous MARKET collector: watches the discovered universe over Bybit's
public WebSocket and turns confirmed klines into canonical closed 1-minute
candles.

This is a long-lived application capability, not a request-driven one. It
is started once from the FastAPI lifespan and runs for the life of the
process; Vitals (and, later, Sniffer) only ever read its status — they
never start, stop, or otherwise drive it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.market import ClosedCandle, Instrument
from app.integrations.bybit.schemas import BybitKlineData
from app.integrations.bybit.ws_client import BybitKlineWebSocketClient
from app.services.candle_conversion import to_closed_candle

logger = logging.getLogger(__name__)

_KLINE_INTERVAL = "1"

# Bybit documents no per-connection topic limit for the linear (futures)
# category, but sharding the universe across several connections keeps any
# one connection's subscribe payload small and, more importantly, keeps a
# single connection's disconnect/reconnect from blacking out the whole
# universe at once.
_SYMBOLS_PER_CONNECTION = 200

# Comfortably under Bybit's documented ~21,000-character args-array limit
# for a single subscribe request.
_SUBSCRIBE_BATCH_SIZE = 50

ClosedCandleSink = Callable[[ClosedCandle], "Awaitable[None] | None"]


@dataclass
class MarketCollectorStatus:
    running: bool = False
    connections_total: int = 0
    connections_active: int = 0
    symbols_subscribed: int = 0
    candles_received: int = 0
    last_candle_at: datetime | None = None


def log_candle_sink(candle: ClosedCandle) -> None:
    """Default sink: the canonical boundary for closed candles this milestone.

    No persistence yet (that's a later milestone) — this just makes closed
    candles observable in logs as they cross into the application.
    """
    logger.debug(
        "closed_candle_received",
        extra={
            "exchange": candle.exchange,
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "open_time": candle.open_time.isoformat(),
            "close": str(candle.close),
        },
    )


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class MarketCollector:
    def __init__(
        self,
        market_universe: Any,  # MarketUniverseService, duck-typed for testability
        ws_base_url: str,
        sink: ClosedCandleSink = log_candle_sink,
        *,
        symbols_per_connection: int = _SYMBOLS_PER_CONNECTION,
        subscribe_batch_size: int = _SUBSCRIBE_BATCH_SIZE,
        client_factory: Callable[[list[str]], Any] | None = None,
    ) -> None:
        self._market_universe = market_universe
        self._ws_base_url = ws_base_url
        self._sink = sink
        self._symbols_per_connection = symbols_per_connection
        self._subscribe_batch_size = subscribe_batch_size
        self._client_factory = client_factory or self._build_client

        self._status = MarketCollectorStatus()
        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def status(self) -> MarketCollectorStatus:
        return self._status

    def _build_client(self, symbols: list[str]) -> BybitKlineWebSocketClient:
        return BybitKlineWebSocketClient(
            url=self._ws_base_url,
            symbols=symbols,
            interval=_KLINE_INTERVAL,
            on_kline=self._handle_kline,
            on_connection_state_change=self._handle_connection_state_change,
            subscribe_batch_size=self._subscribe_batch_size,
        )

    async def start(self) -> None:
        instruments: list[Instrument] = []
        try:
            instruments = await self._market_universe.discover_universe()
        except Exception:  # noqa: BLE001 - Bybit REST must not block collector startup
            logger.exception("market_collector_universe_discovery_failed")

        symbols = [instrument.symbol for instrument in instruments]
        shards = _chunked(symbols, self._symbols_per_connection) if symbols else []

        self._stop_event = asyncio.Event()
        self._status = MarketCollectorStatus(
            running=True,
            connections_total=len(shards),
            symbols_subscribed=len(symbols),
        )
        self._tasks = []

        for shard in shards:
            client = self._client_factory(shard)
            self._tasks.append(asyncio.create_task(client.run(self._stop_event)))

        logger.info(
            "market_collector_started",
            extra={"symbols": len(symbols), "connections": len(shards)},
        )

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._status.running = False
        self._status.connections_active = 0
        logger.info("market_collector_stopped")

    async def add_symbols(self, symbols: list[str]) -> None:
        """Start watching additional symbols without disturbing existing
        connections — called when universe reconciliation discovers newly
        eligible instruments (M3 section 17). Purely additive: existing
        shards are never torn down or resubscribed."""
        if not symbols or self._stop_event is None:
            return

        new_shards = _chunked(symbols, self._symbols_per_connection)
        for shard in new_shards:
            client = self._client_factory(shard)
            self._tasks.append(asyncio.create_task(client.run(self._stop_event)))

        self._status.connections_total += len(new_shards)
        self._status.symbols_subscribed += len(symbols)
        logger.info(
            "market_collector_symbols_added",
            extra={"symbols": len(symbols), "new_connections": len(new_shards)},
        )

    async def _handle_kline(self, symbol: str, kline: BybitKlineData) -> None:
        if not kline.confirm:
            return

        candle = to_closed_candle(symbol, kline)
        self._status.candles_received += 1
        self._status.last_candle_at = candle.close_time

        result = self._sink(candle)
        if asyncio.iscoroutine(result):
            await result

    def _handle_connection_state_change(self, connected: bool) -> None:
        if connected:
            self._status.connections_active += 1
        else:
            self._status.connections_active = max(0, self._status.connections_active - 1)
