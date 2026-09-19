"""Thin WebSocket boundary around Bybit's public v5 kline stream.

Mirrors the REST boundary (`app.integrations.bybit.client`): isolates
transport concerns — connecting, subscribing in batches, heartbeating,
reconnecting — so nothing outside this module ever opens a Bybit WebSocket
or sees a raw Bybit WS payload. Scoped to the kline topic used today; other
public topics (tickers, trades, orderbook) can be added alongside this
without redesigning it.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import websockets
from pydantic import ValidationError

from app.integrations.bybit.schemas import BybitKlineData, BybitKlineMessage

logger = logging.getLogger(__name__)

OnKline = Callable[[str, BybitKlineData], "Awaitable[None] | None"]
OnConnectionStateChange = Callable[[bool], None]


def _chunked(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def parse_kline_topic_symbol(topic: str) -> str | None:
    """Extract the symbol from a `kline.{interval}.{symbol}` topic string."""
    parts = topic.split(".", 2)
    if len(parts) != 3 or parts[0] != "kline":
        return None
    return parts[2]


def parse_kline_message(payload: object) -> list[tuple[str, BybitKlineData]]:
    """Parse one decoded Bybit WS frame into `(symbol, kline)` pairs.

    Returns an empty list for anything that isn't a valid kline push (pings,
    subscription acks, malformed payloads) — callers don't need to
    special-case those.
    """
    if not isinstance(payload, dict):
        return []

    topic = payload.get("topic")
    if not isinstance(topic, str):
        return []

    symbol = parse_kline_topic_symbol(topic)
    if symbol is None:
        return []

    try:
        message = BybitKlineMessage.model_validate(payload)
    except ValidationError:
        logger.warning("bybit_ws_kline_parse_failed", extra={"topic": topic})
        return []

    return [(symbol, item) for item in message.data]


class BybitKlineWebSocketClient:
    """One Bybit public WebSocket connection subscribed to 1m kline topics
    for a shard of symbols.

    Owns the full connection lifecycle: connect, subscribe (batched to stay
    well under Bybit's documented per-request args-length limit), heartbeat
    ping, message dispatch, and reconnect-with-backoff on any failure. A
    Bybit/network failure here never raises out of `run` — it logs and
    retries — so it can be run as a long-lived background task without
    risking the rest of the application.
    """

    def __init__(
        self,
        url: str,
        symbols: Sequence[str],
        interval: str,
        on_kline: OnKline,
        on_connection_state_change: OnConnectionStateChange | None = None,
        *,
        subscribe_batch_size: int = 50,
        ping_interval: float = 20.0,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        connector: Callable[[], Any] | None = None,
    ) -> None:
        self._url = url
        self._symbols = list(symbols)
        self._interval = interval
        self._on_kline = on_kline
        self._on_connection_state_change = on_connection_state_change
        self._subscribe_batch_size = subscribe_batch_size
        self._ping_interval = ping_interval
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._connector = connector or (lambda: websockets.connect(self._url))

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until `stop_event` is set, reconnecting with backoff on failure."""
        delay = self._reconnect_initial_delay
        while not stop_event.is_set():
            try:
                await self._connect_and_listen(stop_event)
                delay = self._reconnect_initial_delay
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a WS failure must not crash the app
                logger.warning(
                    "bybit_ws_connection_failed",
                    extra={"symbols": len(self._symbols), "error": str(exc)},
                )

            if stop_event.is_set():
                break

            logger.info("bybit_ws_reconnecting", extra={"delay_seconds": delay})
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max_delay)

    async def _connect_and_listen(self, stop_event: asyncio.Event) -> None:
        async with self._connector() as ws:
            logger.info("bybit_ws_connected", extra={"symbols": len(self._symbols)})
            if self._on_connection_state_change:
                self._on_connection_state_change(True)

            try:
                await self._subscribe(ws)

                ping_task = asyncio.create_task(self._ping_loop(ws, stop_event))
                try:
                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        await self._handle_raw_message(raw)
                finally:
                    ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ping_task
            finally:
                # Only ever paired with a `True` above — a connection attempt
                # that never entered this block never reports "disconnected".
                if self._on_connection_state_change:
                    self._on_connection_state_change(False)

    async def _subscribe(self, ws: Any) -> None:
        topics = [f"kline.{self._interval}.{symbol}" for symbol in self._symbols]
        for batch in _chunked(topics, self._subscribe_batch_size):
            await ws.send(json.dumps({"op": "subscribe", "args": batch}))

    async def _ping_loop(self, ws: Any, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self._ping_interval)
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"op": "ping"}))

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("bybit_ws_malformed_message")
            return

        for symbol, kline in parse_kline_message(payload):
            result = self._on_kline(symbol, kline)
            if asyncio.iscoroutine(result):
                await result
