"""Domain model for MARKET FRAMES — a unified, temporally-legal
cross-sectional snapshot of the active market universe, produced once per
closed UTC minute.

A candle answers "what happened?"; a Market Frame answers "what was
knowable about the whole market at time T?" See
`app.services.frame_synchronizer` for how frames are built, and
`app.models.frame` for the persisted shape these are read from/written to.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class FrameStatus(StrEnum):
    """A frame's lifecycle. Deliberately just these three — see
    `app.services.frame_synchronizer` for the transitions.

    BUILDING: persisted the instant construction starts (before the grace
      period), so an interrupted build is always inspectable, never silent
      corrupt state.
    COMPLETE: every expected instrument had legal 1m/5m/15m/1h context at
      finalization.
    PARTIAL: finalized truthfully with fewer available instruments than
      expected — a valid, usable fact, never faked into COMPLETE.

    A frame never moves backward (COMPLETE/PARTIAL are terminal) and is
    never re-finalized once terminal — see M4 section 15/16: a finalized
    frame is an immutable statement of what MARKET actually had available
    at T, even if later candle recovery could theoretically fill the gap.
    """

    BUILDING = "building"
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class FrameCandleContext:
    """The full OHLCV context for one (instrument, timeframe) selected into
    a frame — fetched by joining a member's stored `open_time` reference
    back to `market_candles` on demand, never duplicated in frame storage."""

    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal


@dataclass(frozen=True)
class MarketFrameMember:
    """One instrument's synchronized, legal context within a frame.

    Present only for instruments where ALL FOUR configured timeframes had a
    legal (close_time <= frame_time) candle at finalization — an instrument
    missing even one timeframe simply has no member (see
    `MarketFrame.available_instruments`).
    """

    instrument_id: int
    symbol: str
    m1: FrameCandleContext
    m5: FrameCandleContext
    m15: FrameCandleContext
    h1: FrameCandleContext


@dataclass(frozen=True)
class MarketFrame:
    """A finalized (or still-building) cross-sectional market snapshot.

    `expected_instruments` is fixed the instant construction begins, from
    the active universe at that moment — never revised retroactively as the
    universe changes later, so completeness stays a stable historical fact
    for an already-finalized frame (M4 section 6).
    """

    frame_time: datetime
    expected_instruments: int
    available_instruments: int
    status: FrameStatus
    created_at: datetime
    finalized_at: datetime | None
    members: list[MarketFrameMember] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        """Fraction in [0, 1]. 1.0 (not an error) when nothing was expected —
        there is nothing to be incomplete about."""
        if self.expected_instruments == 0:
            return 1.0
        return self.available_instruments / self.expected_instruments
