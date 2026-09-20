from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class SnifferInstrument(BaseModel):
    instrument_id: int
    symbol: str
    # Decimal fraction (+0.01 == +1%), never a formatted string — the
    # frontend multiplies by 100 for display. None means Sniffer considered
    # this instrument for return_5m and it was genuinely unavailable (e.g.
    # no exact T-5m candle), never a substituted/approximated value.
    return_5m: Decimal | None
    # Exact canonical 1m anchor at member.m1.open_time - 60 minutes.
    # Also null for older analyses that predate this feature.
    return_1h: Decimal | None
    # rsi_14_{timeframe} — Cutler's RSI(14) (see app/features/rsi.py) over
    # canonical 5m/15m/1h/4h candles, anchored to this frame's
    # already-selected candle for that timeframe. A plain number (e.g.
    # 63.42), never a percentage. None when 15 consecutive closes aren't
    # available for that timeframe at this frame (short history, a gap, or
    # — for rsi_14_4h specifically — this instrument's first 4h bucket
    # hasn't closed yet).
    rsi_14_5m: Decimal | None
    rsi_14_15m: Decimal | None
    rsi_14_1h: Decimal | None
    rsi_14_4h: Decimal | None


class SnifferFrameResponse(BaseModel):
    frame_time: datetime
    analyzed_at: datetime
    instruments_analyzed: int
    instruments: list[SnifferInstrument]


class SnifferStatus(BaseModel):
    # None (-> N/A) until the first analysis has ever completed.
    status: Literal["ok"] | None
    last_scan: datetime | None
    coins_analyzed: int | None
