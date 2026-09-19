"""Shared conversion from Bybit's raw 1m kline shape to the canonical
`ClosedCandle` domain model.

Both the live WebSocket collector and the REST history reconciler produce a
`BybitKlineData` (the WS boundary parses one directly off the wire; the REST
boundary's `parse_kline_history_row` builds an equivalent one from a
history row) and hand it here — so a candle looks identical regardless of
which transport delivered it, which is exactly what makes idempotent
overlap between them safe.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market import ClosedCandle
from app.integrations.bybit.schemas import BybitKlineData

EXCHANGE = "bybit"
TIMEFRAME_1M = "1m"


def to_closed_candle(symbol: str, kline: BybitKlineData) -> ClosedCandle:
    return ClosedCandle(
        exchange=EXCHANGE,
        symbol=symbol,
        timeframe=TIMEFRAME_1M,
        open_time=datetime.fromtimestamp(kline.start / 1000, tz=UTC),
        close_time=datetime.fromtimestamp(kline.end / 1000, tz=UTC),
        open=Decimal(kline.open),
        high=Decimal(kline.high),
        low=Decimal(kline.low),
        close=Decimal(kline.close),
        volume=Decimal(kline.volume),
        turnover=Decimal(kline.turnover),
    )
