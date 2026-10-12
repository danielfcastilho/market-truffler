"""return_{5m,15m,1h,4h,24h} — exact rolling returns, one reusable class
instantiated per lookback (the same "one class, many timeframe instances"
pattern `RsiFeature` uses).

    return_{timeframe} = (current_close / close_{lookback}_ago) - 1

"current_close" is the close of the base (5m) candle Market Frame T
already selected for this instrument (`member.m5.close`) — never a fresh
"latest candle" query, so it inherits the frame's no-look-ahead guarantee
for free. Normally that candle closes exactly at T.

"N ago" is the 5m candle at exactly `member.m5.open_time - lookback` — a
precise time offset, never "the Nth previous row" — so the return always
spans exactly `lookback` of elapsed time, whatever the base timeframe is.
The lookback must therefore be a whole number of base candles (checked at
construction). Missing that exact candle (a gap, not-yet-synced history,
or the instrument simply hadn't been trading that far back) yields `None`
for that instrument — never approximated with the nearest available
candle.

Stored/returned as a plain decimal fraction (+0.01 for +1%), never a
formatted string — presentation multiplies by 100 where needed.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.domain.timeframes import BASE_TIMEFRAME, is_base_multiple
from app.features.base import Feature
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository


class ReturnFeature(Feature):
    def __init__(self, timeframe: str, lookback: timedelta) -> None:
        if not is_base_multiple(lookback):
            raise ValueError(
                f"return lookback {lookback} is not a whole number of {BASE_TIMEFRAME} candles"
            )
        self.name = f"return_{timeframe}"
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

        # Group members by their exact historical target time. In the
        # common case every member shares the same m5.open_time (frame_time
        # - 5 minutes) and this collapses to a single batched query; a
        # member whose latest legal 5m candle happens to be older (a gap)
        # still gets its own exact, correct target — just possibly grouped
        # separately — never approximated.
        targets_by_time: dict[datetime, list[int]] = {}
        for member in frame.members:
            target = member.m5.open_time - self._lookback
            targets_by_time.setdefault(target, []).append(member.instrument_id)

        historical: dict[int, Candle] = {}
        for target_open_time, instrument_ids in targets_by_time.items():
            historical.update(
                await candle_repo.fetch_exact_open_time(
                    BASE_TIMEFRAME, instrument_ids, target_open_time
                )
            )

        results: dict[int, Decimal | None] = {}
        for member in frame.members:
            past = historical.get(member.instrument_id)
            if past is None or past.close <= 0:
                results[member.instrument_id] = None
                continue
            results[member.instrument_id] = (member.m5.close / past.close) - 1

        return results
