"""funding_rate_current / funding_rate_24h_avg — perpetual funding
pressure, sourced from `funding_rate_observations` (see
`app.services.funding_rate_reconciler.FundingRateReconciler` for how that
table is kept bootstrapped/current). Purely descriptive — nothing here
judges a funding level as good/bad/desirable.

Stored as the same raw decimal fraction convention as `return_*`/
`oi_change_*`/`volatility_*` (`0.0001` == +0.01%), signed: unlike
volatility, funding rate has a real direction (longs pay shorts, or vice
versa), and unlike Open Interest, a rate of exactly zero or negative is a
completely ordinary, legitimate reading — never treated as "unavailable".

Both features read `funding_rate_observations` directly against
`frame.frame_time`, the same way `OpenInterestChangeFeature` reads
`open_interest_observations` against it — never a frame member's own
m1/m5/... candle anchor (funding is not a candle-derived measurement),
but still fully no-look-ahead-safe: every query below is bounded to
"at or before" / "no later than" `frame.frame_time`.

`funding_rate_current` = the latest funding observation at or before
`frame.frame_time` — the current funding pressure/cost. `None` when no
observation exists yet at or before the frame (insufficient backfill, or
the instrument genuinely predates any settlement).

`funding_rate_24h_avg` = the plain mean of every observation with
`frame.frame_time - 24h <= funding_time <= frame.frame_time` — smoothing
out individual settlements into a persistent-pressure reading. Each
instrument's own real funding interval (see
`BybitClient.get_funding_rate_history`) decides how many observations
land in that window; a `1h`-interval instrument naturally averages more
points than an `8h`-interval one over the same 24 hours, and that's
correct, not a bug to normalize away. `None` when the window has no
observations at all — never a shortened window or an interpolated value.
"""

from datetime import timedelta
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.features.base import Feature
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository

_AVG_WINDOW = timedelta(hours=24)


class FundingRateCurrentFeature(Feature):
    name = "funding_rate_current"
    # Generous enough that, by the time an instrument is considered ready
    # for this feature, its funding backfill has had time to actually
    # observe at least one real settlement (see module docstring) —
    # matches funding_rate_24h_avg's own window rather than a separate,
    # arbitrarily shorter constant.
    required_history = _AVG_WINDOW

    async def calculate(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository | None = None,
        funding_repo: FundingRateRepository | None = None,
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}
        if funding_repo is None:
            raise ValueError(f"{self.name} requires funding_repo, none was given")

        instrument_ids = [member.instrument_id for member in frame.members]
        current = await funding_repo.fetch_latest_at_or_before_per_instrument(
            instrument_ids, frame.frame_time
        )

        return {
            member.instrument_id: (
                current[member.instrument_id].funding_rate
                if member.instrument_id in current
                else None
            )
            for member in frame.members
        }


class FundingRate24hAvgFeature(Feature):
    name = "funding_rate_24h_avg"
    required_history = _AVG_WINDOW

    async def calculate(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository | None = None,
        funding_repo: FundingRateRepository | None = None,
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}
        if funding_repo is None:
            raise ValueError(f"{self.name} requires funding_repo, none was given")

        instrument_ids = [member.instrument_id for member in frame.members]
        since = frame.frame_time - _AVG_WINDOW
        observations = await funding_repo.fetch_since_per_instrument(
            instrument_ids, since, frame.frame_time
        )

        results: dict[int, Decimal | None] = {}
        for member in frame.members:
            obs = observations.get(member.instrument_id)
            if not obs:
                results[member.instrument_id] = None
                continue
            results[member.instrument_id] = sum(
                (o.funding_rate for o in obs), start=Decimal(0)
            ) / len(obs)

        return results
