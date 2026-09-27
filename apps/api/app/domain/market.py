"""Application-level market domain model — independent of any exchange's wire format."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    """A single tradable instrument in Market Truffler's market universe."""

    symbol: str
    base_coin: str
    quote_coin: str


@dataclass(frozen=True)
class ClosedCandle:
    """A confirmed, closed candle for one instrument at one timeframe.

    Timestamps are the exchange's own (never local receipt time). Identity
    is `(exchange, symbol, timeframe, open_time)` — see `key` — so callers
    can recognize a duplicate delivery of the same candle (e.g. after a
    WebSocket reconnect/resubscribe) without this type doing any
    deduplication itself.
    """

    exchange: str
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal

    @property
    def key(self) -> tuple[str, str, str, datetime]:
        return (self.exchange, self.symbol, self.timeframe, self.open_time)


@dataclass(frozen=True)
class OpenInterestObservation:
    """One point-in-time open interest reading for one instrument, at
    Bybit's own 5-minute bucket granularity (see
    `BybitClient.get_open_interest`) — the OI equivalent of a canonical 1m
    candle: every coarser OI lookback MARKET needs is an exact-timestamp
    offset from this one series, never re-fetched or re-derived per
    timeframe.
    """

    exchange: str
    symbol: str
    observed_at: datetime
    open_interest: Decimal
