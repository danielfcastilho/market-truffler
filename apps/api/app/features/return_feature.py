"""ReturnFeature — one reusable rolling-return implementation shared by
every *new* Return timeframe (15m, 4h, 24h), the same "one class, many
timeframe instances" pattern `RsiFeature` already established.

`return_5m`/`return_1h` (see `return_5m.py`/`return_1h.py`) predate this
generalization and are deliberately left as their own dedicated classes —
each already has an extensive, specific test suite, and there is no
functional reason to touch stable, tested code just to consolidate it.
Every Return feature (old or new) computes the identical formula and
shares the identical no-look-ahead guarantee; only the lookback duration
differs, which is exactly what this class parametrizes.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.features.base import Feature
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository


class ReturnFeature(Feature):
    """`return_{timeframe} = (current_close / close_{lookback}_ago) - 1`.

    "current_close" is always the frame member's own selected m1 candle
    (`member.m1.close`) — never a fresh `market_candles` query, so this
    inherits the frame's no-look-ahead guarantee for free, exactly like
    `Return5m`/`Return1h`. "N ago" is the canonical 1m candle at exactly
    `member.m1.open_time - lookback` — a precise time offset, never "the
    Nth previous row". Missing that exact candle (a gap, not-yet-synced
    history, or the instrument simply hadn't been trading that far back)
    yields `None` for that instrument — never approximated with the
    nearest available candle.
    """

    def __init__(self, timeframe: str, lookback: timedelta) -> None:
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

        # Group members by their exact historical target time — the common
        # case (every member sharing the same m1.open_time) collapses to a
        # single batched query, never one query per instrument.
        targets_by_time: dict[datetime, list[int]] = {}
        for member in frame.members:
            target = member.m1.open_time - self._lookback
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
