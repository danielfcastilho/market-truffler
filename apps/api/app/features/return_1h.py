"""return_1h — exact rolling 60-minute return.

    return_1h = (current_close / close_1_hour_ago) - 1

"current_close" is the close of the canonical 1m candle Market Frame T
already selected for this instrument (`member.m1.close`) — never a fresh
"latest candle" query, so it inherits M4's no-look-ahead guarantee for
free.

"60 minutes ago" is the canonical 1m candle at exactly
`member.m1.open_time - 60 minutes` — a precise time offset, never "the 60th
previous row". If that exact candle doesn't exist (a gap, a not-yet-synced
history, or the instrument simply hadn't been trading 60 minutes earlier),
the result is `None` (unavailable) for that instrument — never
approximated with the nearest available candle.

Stored/returned as a plain decimal fraction (+0.01 for +1%), never a
formatted string — presentation multiplies by 100 where needed.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.features.base import Feature
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository

_LOOKBACK = timedelta(minutes=60)


class Return1h(Feature):
    name = "return_1h"

    async def calculate(
        self, frame: MarketFrame, candle_repo: CandleRepository
    ) -> dict[int, Decimal | None]:
        if not frame.members:
            return {}

        # Group members by their exact historical target time. In the
        # common case every member shares the same m1.open_time (frame_time
        # - 1 minute) and this collapses to a single batched query; a
        # member whose latest legal 1m candle happens to be older (a gap)
        # still gets its own exact, correct target — just possibly grouped
        # separately — never approximated.
        targets_by_time: dict[datetime, list[int]] = {}
        for member in frame.members:
            target = member.m1.open_time - _LOOKBACK
            targets_by_time.setdefault(target, []).append(member.instrument_id)

        historical: dict[int, Candle] = {}
        for target_open_time, instrument_ids in targets_by_time.items():
            historical.update(
                await candle_repo.fetch_exact_open_time("1m", instrument_ids, target_open_time)
            )

        results: dict[int, Decimal | None] = {}
        for member in frame.members:
            past = historical.get(member.instrument_id)
            if past is None or past.close <= 0:
                results[member.instrument_id] = None
                continue
            results[member.instrument_id] = (member.m1.close / past.close) - 1

        return results
