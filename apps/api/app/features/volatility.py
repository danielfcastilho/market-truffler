"""volatility_{15m,1h,4h,24h} — ATR(14) normalized by current price, one
reusable class instantiated per timeframe (the same "one class, many
timeframe instances" pattern `RsiFeature`/`OpenInterestChangeFeature`
already established).

    ATR% = ATR(14) / current_price

Stored as the same raw decimal fraction convention as `return_*`/
`oi_change_*` (`0.05` == 5%), but unlike those, always non-negative:
volatility has no direction, so there is deliberately no sign. Purely
descriptive — nothing here judges a level of volatility as
good/bad/desirable.

CONVENTION: a plain, unweighted mean of the 14 most recent True Range
values — not Wilder's original recursive smoothing — for exactly the
same reason `app.features.rsi` uses Cutler's RSI instead of Wilder's:
recursive smoothing carries every prior period's average forward
indefinitely, so its value depends on wherever smoothing happened to
start, with no principled "correct" starting point for a value computed
fresh per frame. This "simple ATR" is fully determined by exactly the
last 15 consecutive closed candles of the given timeframe:

    TR_i = max(high_i - low_i, |high_i - close_(i-1)|, |low_i - close_(i-1)|)
    ATR(14) = mean(TR_i for the 14 most recent TR values)

HISTORY REQUIREMENT: exactly 15 consecutive closed candles of the named
timeframe, ending exactly at the frame's already-selected anchor candle
for that timeframe (`member.m15/h1/h4/h24`) — the identical requirement
`RsiFeature` has, via the shared `app.features.candle_window` helper.
Fewer than 15, any gap, or a missing anchor (e.g. `h24` not yet available
for this instrument/frame) all yield `None` — never a shortened period,
an interpolated value, or the nearest available candle.
"""

from collections.abc import Callable, Sequence
from datetime import timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, MarketFrame, MarketFrameMember
from app.features.base import Feature
from app.features.candle_window import fetch_verified_windows
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.open_interest_repository import OpenInterestRepository


def compute_atr_14(candles: Sequence[Candle]) -> Decimal | None:
    """Simple ATR(14) from exactly 15 consecutive closed candles,
    oldest-to-newest. Returns None if given any other count — this
    function does not itself check for gaps in time; callers (see
    `VolatilityFeature`) must only ever pass a verified-contiguous
    window."""
    if len(candles) != 15:
        return None

    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles[:-1], candles[1:], strict=True)
    ]
    return sum(true_ranges, start=Decimal(0)) / len(true_ranges)


class VolatilityFeature(Feature):
    """One reusable ATR%-based volatility implementation shared by every
    timeframe — only the timeframe label, its candle duration, and how to
    read the frame's already-selected anchor candle for it differ per
    instance (see `app.features.engine.FEATURES`)."""

    def __init__(
        self,
        timeframe: str,
        duration: timedelta,
        anchor: Callable[[MarketFrameMember], FrameCandleContext | None],
    ) -> None:
        self.name = f"volatility_{timeframe}"
        self._timeframe = timeframe
        self._duration = duration
        self._anchor = anchor
        self.required_history = duration * 15

    async def calculate(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository | None = None,
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}

        windows = await fetch_verified_windows(
            candle_repo, self._timeframe, self._duration, frame.members, self._anchor
        )

        results: dict[int, Decimal | None] = {}
        for member in frame.members:
            candles = windows.get(member.instrument_id)
            if candles is None:
                results[member.instrument_id] = None
                continue
            atr = compute_atr_14(candles)
            current_price = candles[-1].close
            if atr is None or current_price <= 0:
                results[member.instrument_id] = None
                continue
            results[member.instrument_id] = atr / current_price

        return results
