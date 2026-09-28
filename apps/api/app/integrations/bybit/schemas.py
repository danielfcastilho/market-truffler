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


class BybitOpenInterestEntry(BaseModel):
    """One bucket from `/v5/market/open-interest` — already an object
    (unlike kline's positional array rows), so no separate row-parser is
    needed: Pydantic validates this shape directly."""

    openInterest: str
    timestamp: str


class OpenInterestHistoryResult(BaseModel):
    """`/v5/market/open-interest`'s result envelope. `list` is sorted
    newest-first, one entry per `intervalTime` bucket (MARKET always
    requests `5min` — see `BybitClient.get_open_interest`)."""

    category: str
    symbol: str
    list: list[BybitOpenInterestEntry]
    nextPageCursor: str = ""


class BybitFundingRateEntry(BaseModel):
    """One settled funding event from `/v5/market/funding/history` —
    already an object, like `BybitOpenInterestEntry`. `fundingRate` is
    Bybit's own raw fraction (e.g. `"0.0001"` for +0.01%), already in the
    convention this app stores every rate/return-like feature in, so it
    is cast straight to `Decimal` with no rescaling."""

    symbol: str
    fundingRate: str
    fundingRateTimestamp: str


class FundingRateHistoryResult(BaseModel):
    """`/v5/market/funding/history`'s result envelope. `list` is sorted
    newest-first, one entry per actual funding settlement — unlike Open
    Interest, there is no fixed `intervalTime` to request: each
    instrument's contract settles at its own real interval (commonly
    1h/2h/4h/8h), and Bybit reports the true settlement timestamps
    directly rather than a bucketed series."""

    category: str
    list: list[BybitFundingRateEntry]
