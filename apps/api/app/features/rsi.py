"""rsi_14_5m / rsi_14_15m / rsi_14_1h / rsi_14_4h — standard RSI(14), one
per canonical timeframe, computed from the materialized MARKET candles of
that timeframe (never reconstructed inside Sniffer — see
`app.services.candle_aggregation`).

CONVENTION: Cutler's RSI (a.k.a. "RSI2"). Average gain/loss is a plain,
unweighted mean over the most recent 14 price changes (15 consecutive
closes) — not Wilder's original recursive smoothing. Wilder's recursion
carries every prior period's average forward indefinitely, so its value
depends on however far back smoothing happened to start; there is no
principled "correct" starting point for a value computed fresh per frame.
Cutler's variant is fully determined by exactly the last 15 consecutive
closes, giving the same determinism guarantee return_5m/return_1h already
rely on: the same frame_time always produces the same RSI, and the choice
of the frame's exact anchor candle (never "now") is what makes it
no-look-ahead safe.

    change_i = close_i - close_(i-1), for the 14 most recent closes
    avg_gain = mean(change_i for change_i > 0, else 0)   over 14 changes
    avg_loss = mean(-change_i for change_i < 0, else 0)  over 14 changes
    RS  = avg_gain / avg_loss
    RSI = 100 - 100 / (1 + RS)

Edge cases: avg_loss == 0 and avg_gain > 0 -> RSI = 100 (no losses in the
window at all). avg_gain == 0 and avg_loss == 0 -> RSI = 50 (every close
was flat — genuinely neutral, not fabricated as either extreme).

HISTORY REQUIREMENT: exactly 15 consecutive closed candles of the named
timeframe, each exactly one timeframe-duration apart, ending exactly at the
frame's already-selected anchor candle for that timeframe
(`member.m5/m15/h1/h4`). Fewer than 15, any gap, or a missing anchor (e.g.
`h4` not yet available for this instrument/frame) all yield `None` — never
a shortened period, an interpolated value, or the nearest available
candle.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, MarketFrame, MarketFrameMember
from app.features.base import Feature
from app.repositories.candle_repository import CandleRepository

_PERIOD = 14
REQUIRED_CLOSES = _PERIOD + 1  # 15 consecutive closes -> 14 changes


def compute_rsi_14(closes: Sequence[Decimal]) -> Decimal | None:
    """Cutler's RSI(14) from exactly `REQUIRED_CLOSES` (15) consecutive
    closes, oldest-to-newest. Returns None if given any other count — this
    function does not itself check for gaps in time; callers (see
    `RsiFeature`) must only ever pass a verified-contiguous window."""
    if len(closes) != REQUIRED_CLOSES:
        return None

    gains = Decimal(0)
    losses = Decimal(0)
    for previous, current in zip(closes[:-1], closes[1:], strict=True):
        change = current - previous
        if change > 0:
            gains += change
        elif change < 0:
            losses += -change

    avg_gain = gains / _PERIOD
    avg_loss = losses / _PERIOD

    if avg_gain == 0 and avg_loss == 0:
        return Decimal(50)
    if avg_loss == 0:
        return Decimal(100)

    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (1 + rs))


def _expected_window(anchor_time: datetime, duration: timedelta) -> list[datetime]:
    """The exact 15 open_times a legal window must have: `anchor_time` and
    the 14 preceding ones, each exactly `duration` apart, oldest first."""
    return [anchor_time - duration * i for i in range(REQUIRED_CLOSES - 1, -1, -1)]


class RsiFeature(Feature):
    """One reusable RSI(14) implementation shared by every timeframe — only
    the timeframe label, its candle duration, and how to read the frame's
    already-selected anchor candle for it differ per instance (see
    `app.features.engine.FEATURES`)."""

    def __init__(
        self,
        timeframe: str,
        duration: timedelta,
        anchor: Callable[[MarketFrameMember], FrameCandleContext | None],
    ) -> None:
        self.name = f"rsi_14_{timeframe}"
        self._timeframe = timeframe
        self._duration = duration
        self._anchor = anchor

    async def calculate(
        self, frame: MarketFrame, candle_repo: CandleRepository
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}

        # Every member's anchor open_time is exactly the timeframe candle
        # Market Frame T already legally selected for it — never a fresh
        # "latest candle" query — so this inherits the frame's no-look-ahead
        # guarantee for free, exactly like return_5m/return_1h.
        anchor_by_instrument: dict[int, datetime] = {}
        for member in frame.members:
            context = self._anchor(member)
            if context is not None:
                anchor_by_instrument[member.instrument_id] = context.open_time

        # Group instruments by identical anchor time so the common case
        # (every member sharing the same anchor) collapses to one batched
        # query — never one query per instrument.
        instruments_by_anchor: dict[datetime, list[int]] = {}
        for instrument_id, anchor_time in anchor_by_instrument.items():
            instruments_by_anchor.setdefault(anchor_time, []).append(instrument_id)

        closes_by_instrument: dict[int, list[Decimal]] = {}
        for anchor_time, instrument_ids in instruments_by_anchor.items():
            candles_by_instrument = await candle_repo.fetch_latest_n_closed_per_instrument(
                self._timeframe, instrument_ids, anchor_time, REQUIRED_CLOSES
            )
            expected = _expected_window(anchor_time, self._duration)
            for instrument_id, candles in candles_by_instrument.items():
                if [c.open_time for c in candles] != expected:
                    continue  # short history or a gap — never a shortened/interpolated window
                closes_by_instrument[instrument_id] = [c.close for c in candles]

        return {
            member.instrument_id: (
                compute_rsi_14(closes_by_instrument[member.instrument_id])
                if member.instrument_id in closes_by_instrument
                else None
            )
            for member in frame.members
        }
