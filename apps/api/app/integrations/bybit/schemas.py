"""Raw shapes of Bybit's v5 REST responses.

Kept separate from the app's own domain model (`app.domain.market`) so a
Bybit field rename only ever touches this module.
"""

from pydantic import BaseModel


class BybitInstrument(BaseModel):
    symbol: str
    baseCoin: str
    quoteCoin: str
    settleCoin: str
    contractType: str
    status: str


class InstrumentsInfoResult(BaseModel):
    category: str
    list: list[BybitInstrument]
    nextPageCursor: str = ""


class BybitKlineData(BaseModel):
    """One kline entry from a `kline.{interval}.{symbol}` WS push.

    `confirm=True` means the candle has closed; otherwise it is still
    forming and updating. `start`/`end`/`timestamp` are exchange epoch
    milliseconds.
    """

    start: int
    end: int
    interval: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    turnover: str
    confirm: bool
    timestamp: int


class BybitKlineMessage(BaseModel):
    topic: str
    type: str
    data: list[BybitKlineData]


class KlineHistoryResult(BaseModel):
    """`/v5/market/kline`'s result envelope.

    Each row in `list` is a 7-element array
    `[startTime, open, high, low, close, volume, turnover]` (all strings
    except startTime), sorted newest-first — never an object like the
    WebSocket push. `parse_kline_history_row` below turns one row into the
    same `BybitKlineData` shape the WS boundary produces, so downstream code
    (ingestion, aggregation) never needs to know which transport a 1m
    candle came from.
    """

    category: str
    symbol: str
    list: list[list[str]]


def parse_kline_history_row(row: list[str]) -> BybitKlineData:
    """Parse one 1-minute row from `/v5/market/kline` (`interval=1` only —
    MARKET never fetches other timeframes from REST; see invariant B)."""
    start_ms = int(row[0])
    return BybitKlineData(
        start=start_ms,
        end=start_ms + 60_000 - 1,
        interval="1",
        open=row[1],
        high=row[2],
        low=row[3],
        close=row[4],
        volume=row[5],
        turnover=row[6],
        confirm=True,  # historical rows are, by definition, already closed
        timestamp=start_ms,
    )
