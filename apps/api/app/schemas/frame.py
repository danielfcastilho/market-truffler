from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class FrameCandleContext(BaseModel):
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal


class FrameMember(BaseModel):
    instrument_id: int
    symbol: str
    m1: FrameCandleContext
    m5: FrameCandleContext
    m15: FrameCandleContext
    h1: FrameCandleContext


class MarketFrameResponse(BaseModel):
    frame_time: datetime
    expected_instruments: int
    available_instruments: int
    completeness: float
    status: str
    created_at: datetime
    finalized_at: datetime | None
    members: list[FrameMember]
