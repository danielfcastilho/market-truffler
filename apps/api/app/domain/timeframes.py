"""MARKET's candle timeframes — the single source of truth for which
timeframes exist and how long each one is.

5m is the canonical base timeframe: the only one fetched from Bybit
(WebSocket `kline.5.{symbol}` live, REST `/v5/market/kline?interval=5`
for history). Every coarser timeframe is derived locally from complete
sets of stored 5m candles (see `app.services.candle_aggregation`).
There is deliberately no finer resolution anywhere in the system.

TIMESTAMP SEMANTICS (every timeframe): a candle is identified by its
`open_time`; it covers the half-open interval `[open_time, open_time +
duration)` and is stored with a `close_time` just before `open_time +
duration` (1ms before for Bybit-observed candles, Bybit's own convention;
1µs before for derived ones). It becomes knowable — "closed" — at instant
`open_time + duration`. So the 5m candle opened at 10:00 UTC is usable by
a decision at 10:05 UTC or later, and never by one before 10:05.

All boundaries are UTC-epoch-aligned (a 1h candle always opens at hh:00,
a 4h candle at 00/04/08/12/16/20:00, etc.), never relative to when the
application started.
"""

from datetime import UTC, datetime, timedelta

BASE_TIMEFRAME = "5m"
BASE_DURATION = timedelta(minutes=5)
#: Bybit's `interval` parameter / WebSocket topic segment for `BASE_TIMEFRAME`.
BYBIT_BASE_INTERVAL = "5"

#: Every stored timeframe, finest first. `BASE_TIMEFRAME` must stay first.
TIMEFRAMES: dict[str, timedelta] = {
    "5m": BASE_DURATION,
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}


def floor_to_boundary(dt: datetime, duration: timedelta) -> datetime:
    """The latest UTC-epoch-aligned `duration` boundary at or before `dt`."""
    step = int(duration.total_seconds())
    return datetime.fromtimestamp((int(dt.timestamp()) // step) * step, tz=UTC)


def is_aligned(dt: datetime, duration: timedelta = BASE_DURATION) -> bool:
    """Whether `dt` falls exactly on a `duration` boundary."""
    return floor_to_boundary(dt, duration) == dt


def is_base_multiple(duration: timedelta) -> bool:
    """Whether `duration` is a positive whole number of base candles —
    the only durations a frame-relative lookback/horizon can be measured
    over exactly."""
    return duration > timedelta(0) and duration % BASE_DURATION == timedelta(0)
