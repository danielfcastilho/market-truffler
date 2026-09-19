"""Runs every configured feature against a finalized frame and assembles
the per-instrument results.

`FEATURES` is a plain, explicit tuple — not a registry, not dynamically
discovered. Adding metric #2 means writing `app/features/volatility.py` (or
similar) and adding one entry here.
"""

from datetime import datetime

from app.domain.frame import MarketFrame
from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.features.return_5m import Return5m
from app.repositories.candle_repository import CandleRepository

FEATURES = (Return5m(),)


class FeatureEngine:
    async def run(
        self, frame: MarketFrame, candle_repo: CandleRepository, *, analyzed_at: datetime
    ) -> SnifferFrameResult:
        # {feature_name: {instrument_id: value | None}}
        per_feature = {
            feature.name: await feature.calculate(frame, candle_repo) for feature in FEATURES
        }

        instruments = [
            SnifferInstrumentResult(
                instrument_id=member.instrument_id,
                symbol=member.symbol,
                features={
                    name: values.get(member.instrument_id) for name, values in per_feature.items()
                },
            )
            for member in frame.members
        ]

        return SnifferFrameResult(
            frame_time=frame.frame_time, analyzed_at=analyzed_at, instruments=instruments
        )
