"""oi_change_{5m,15m,1h,4h,24h} — percentage change in Open Interest over
a rolling lookback, one reusable class instantiated per timeframe (the
same "one class, many timeframe instances" pattern `RsiFeature` uses).

    oi_change_{timeframe} = (current_oi / previous_oi) - 1

Stored as a raw decimal fraction (e.g. `0.05` for +5%), exactly like
`return_5m`/`return_1h`/`ReturnFeature` — never pre-multiplied by 100 —
so every existing "signed percentage" presentation convention (frontend
formatting, sign coloring) applies to OI change unchanged; only the
underlying source (Open Interest observations, not candle closes)
differs. `(current / previous - 1) * 100` is the same value in
percentage-space, just realized here in the codebase's existing
fraction convention rather than a second, parallel one.

"Current OI" is the latest Open Interest observation at or before
`frame.frame_time` (`OpenInterestRepository.
fetch_latest_at_or_before_per_instrument`) — the OI equivalent of a
frame's already-selected candle anchor, so this inherits the frame's
no-look-ahead guarantee exactly like every other feature. "Previous OI"
is the observation at exactly `current.observed_at - lookback` — a
precise time offset from that anchor (never "now - lookback", and never
"the Nth previous row"), mirroring `ReturnFeature`'s own anchor-then-
exact-offset pattern. Missing either observation (a gap, insufficient
backfill, or the instrument simply not tracked that far back) yields
`None` for that instrument — never approximated or interpolated.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.features.base import Feature
from app.models.open_interest import OpenInterestObservation
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository


class OpenInterestChangeFeature(Feature):
    def __init__(self, timeframe: str, lookback: timedelta) -> None:
        self.name = f"oi_change_{timeframe}"
        self._lookback = lookback
        self.required_history = lookback

    async def calculate(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository | None = None,
        funding_repo: FundingRateRepository | None = None,
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}
        if oi_repo is None:
            raise ValueError(f"{self.name} requires oi_repo, none was given")

        instrument_ids = [member.instrument_id for member in frame.members]
        current = await oi_repo.fetch_latest_at_or_before_per_instrument(
            instrument_ids, frame.frame_time
        )

        # Group by each instrument's own current-anchor time minus the
        # lookback — the common case (every member sharing the same
        # current anchor) collapses to one batched query, never one per
        # instrument.
        targets_by_time: dict[datetime, list[int]] = {}
        for instrument_id, observation in current.items():
            target = observation.observed_at - self._lookback
            targets_by_time.setdefault(target, []).append(instrument_id)

        previous: dict[int, OpenInterestObservation] = {}
        for target_time, ids in targets_by_time.items():
            previous.update(await oi_repo.fetch_exact_observed_at(ids, target_time))

        results: dict[int, Decimal | None] = {}
        for member in frame.members:
            cur = current.get(member.instrument_id)
            prev = previous.get(member.instrument_id)
            if cur is None or prev is None or prev.open_interest <= 0:
                results[member.instrument_id] = None
                continue
            results[member.instrument_id] = (cur.open_interest / prev.open_interest) - 1

        return results
