"""Shared "N most recent, exactly-consecutive closed candles ending at a
frame-legal anchor" resolution — the batched, no-look-ahead-safe window
`VolatilityFeature` builds its ATR(14) input from (see
`app.features.rsi` for the historically-first implementation of this
same pattern; kept independent there rather than refactored onto this
module, since RSI's own version is stable and already extensively
tested — this module exists for new features to build on, not to
retroactively consolidate old ones).
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain.frame import FrameCandleContext, MarketFrameMember
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository

REQUIRED_CLOSES = 15  # 14 changes/ranges between consecutive closes, plus the anchor itself


def expected_window(anchor_time: datetime, duration: timedelta) -> list[datetime]:
    """The exact `REQUIRED_CLOSES` open_times a legal window must have:
    `anchor_time` and the `REQUIRED_CLOSES - 1` preceding ones, each
    exactly `duration` apart, oldest first."""
    return [anchor_time - duration * i for i in range(REQUIRED_CLOSES - 1, -1, -1)]


async def fetch_verified_windows(
    candle_repo: CandleRepository,
    timeframe: str,
    duration: timedelta,
    members: list[MarketFrameMember],
    anchor: Callable[[MarketFrameMember], FrameCandleContext | None],
) -> dict[int, list[Candle]]:
    """`{instrument_id: [REQUIRED_CLOSES candles, oldest-to-newest]}` for
    every member whose window is exactly the expected `REQUIRED_CLOSES`
    consecutive candles ending at its own frame-anchored candle for this
    timeframe. A short history, a gap in the middle, or a missing anchor
    (e.g. `member.h24` not yet available) all simply leave that
    instrument absent from the result — never a shortened, interpolated,
    or nearest-candle substitute.

    Members are grouped by identical anchor `open_time` before querying
    (the common case — every member sharing the same frame-relative
    anchor — collapses to one query per timeframe, never one per
    instrument), mirroring `return_5m`/`return_1h`/`RsiFeature`'s
    existing batching pattern exactly.
    """
    anchor_by_instrument: dict[int, datetime] = {}
    for member in members:
        context = anchor(member)
        if context is not None:
            anchor_by_instrument[member.instrument_id] = context.open_time

    instruments_by_anchor: dict[datetime, list[int]] = {}
    for instrument_id, anchor_time in anchor_by_instrument.items():
        instruments_by_anchor.setdefault(anchor_time, []).append(instrument_id)

    windows: dict[int, list[Candle]] = {}
    for anchor_time, instrument_ids in instruments_by_anchor.items():
        candles_by_instrument = await candle_repo.fetch_latest_n_closed_per_instrument(
            timeframe, instrument_ids, anchor_time, REQUIRED_CLOSES
        )
        expected = expected_window(anchor_time, duration)
        for instrument_id, candles in candles_by_instrument.items():
            if [c.open_time for c in candles] == expected:
                windows[instrument_id] = candles

    return windows
