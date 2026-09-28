"""relative_volume_{15m,1h,4h} — current-window volume relative to this
instrument's own recent normal volume for an equivalent window, one
reusable class instantiated per timeframe (the same "one class, many
timeframe instances" pattern `RsiFeature`/`VolatilityFeature` already
established).

    relative_volume_{timeframe} = current_volume / baseline_volume

Expressed as a plain ratio (never a percentage): `1.0` is normal, `2.0`
is double the normal volume, `0.5` is half. Purely descriptive — nothing
here judges a level of participation as good/bad/desirable.

BASELINE: the mean volume of the 14 *preceding* candles of the same
timeframe, immediately before the current (most recent) one — reusing
the exact same "15 consecutive closed candles ending at the frame's
already-selected anchor" window `VolatilityFeature` builds via
`app.features.candle_window`, just read differently: the newest candle in
the window is "current volume", and the other 14 are the trailing
baseline it's compared against. This keeps RVOL's own no-look-ahead and
insufficient-history behavior byte-for-byte identical to Volatility's
(see that module) with no separate windowing logic to maintain.

    current_volume  = window[-1].volume   (the anchor candle itself)
    baseline_volume = mean(c.volume for c in window[:-1])   (the 14 before it)

Fewer than 15 consecutive candles, any gap, or a missing anchor (e.g.
`h4` not yet available for this instrument/frame) all yield `None` —
never a shortened baseline, an interpolated value, or the nearest
available candle. A `baseline_volume` of exactly zero (e.g. a newly
listed, untraded instrument) also yields `None` rather than an infinite
or fabricated ratio.

Deliberately no `relative_volume_24h`: not part of this feature set yet.
"""

from collections.abc import Callable, Sequence
from datetime import timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, MarketFrame, MarketFrameMember
from app.features.base import Feature
from app.features.candle_window import fetch_verified_windows
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository


def compute_relative_volume(candles: Sequence[Candle]) -> Decimal | None:
    """Current volume over the trailing baseline, from exactly 15
    consecutive closed candles, oldest-to-newest. Returns `None` if given
    any other count, or if the baseline (the mean of the first 14) is
    zero — this function does not itself check for gaps in time; callers
    (see `RelativeVolumeFeature`) must only ever pass a verified-
    contiguous window."""
    if len(candles) != 15:
        return None

    baseline_candles = candles[:-1]
    baseline = sum((c.volume for c in baseline_candles), start=Decimal(0)) / len(baseline_candles)
    if baseline <= 0:
        return None

    return candles[-1].volume / baseline


class RelativeVolumeFeature(Feature):
    """One reusable RVOL implementation shared by every timeframe — only
    the timeframe label, its candle duration, and how to read the frame's
    already-selected anchor candle for it differ per instance (see
    `app.features.engine.FEATURES`)."""

    def __init__(
        self,
        timeframe: str,
        duration: timedelta,
        anchor: Callable[[MarketFrameMember], FrameCandleContext | None],
    ) -> None:
        self.name = f"relative_volume_{timeframe}"
        self._timeframe = timeframe
        self._duration = duration
        self._anchor = anchor
        self.required_history = duration * 15

    async def calculate(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository | None = None,
        funding_repo: FundingRateRepository | None = None,
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
            results[member.instrument_id] = compute_relative_volume(candles)

        return results
