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
