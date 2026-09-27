"""Runs every configured feature against a finalized frame and assembles
the per-instrument results.

`FEATURES` is a plain, explicit tuple — not a registry, not dynamically
discovered. Each feature is listed explicitly below.
"""

from datetime import datetime, timedelta

from app.domain.frame import MarketFrame
from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.features.open_interest_change import OpenInterestChangeFeature
from app.features.return_1h import Return1h
from app.features.return_5m import Return5m
from app.features.return_feature import ReturnFeature
from app.features.rsi import RsiFeature
from app.features.volatility import VolatilityFeature
from app.repositories.candle_repository import CandleRepository
from app.repositories.open_interest_repository import OpenInterestRepository

FEATURES = (
    Return5m(),
    ReturnFeature("15m", timedelta(minutes=15)),
    Return1h(),
    ReturnFeature("4h", timedelta(minutes=240)),
    ReturnFeature("24h", timedelta(minutes=1440)),
    RsiFeature("5m", timedelta(minutes=5), lambda member: member.m5),
    RsiFeature("15m", timedelta(minutes=15), lambda member: member.m15),
    RsiFeature("1h", timedelta(minutes=60), lambda member: member.h1),
    RsiFeature("4h", timedelta(minutes=240), lambda member: member.h4),
    OpenInterestChangeFeature("5m", timedelta(minutes=5)),
    OpenInterestChangeFeature("15m", timedelta(minutes=15)),
    OpenInterestChangeFeature("1h", timedelta(minutes=60)),
    OpenInterestChangeFeature("4h", timedelta(minutes=240)),
    OpenInterestChangeFeature("24h", timedelta(minutes=1440)),
    VolatilityFeature("15m", timedelta(minutes=15), lambda member: member.m15),
    VolatilityFeature("1h", timedelta(minutes=60), lambda member: member.h1),
    VolatilityFeature("4h", timedelta(minutes=240), lambda member: member.h4),
    VolatilityFeature("24h", timedelta(minutes=1440), lambda member: member.h24),
)

#: The single centralized "how much reconciled history does Sniffer
#: currently need" figure — the max `required_history` across every
#: currently-configured feature. `volatility_24h`'s 15-candle/15-day
#: window is now the deepest lookback (well past rsi_14_4h's 60h) —
#: adding a feature with an even longer lookback to `FEATURES` above
#: automatically raises this further, and with it both
#: `app.services.open_interest_reconciler`'s bootstrap target/retention
#: and `app.services.symbol_readiness`'s READY threshold, with no other
#: change needed anywhere in the reconciliation system.
REQUIRED_WARMUP: timedelta = max(feature.required_history for feature in FEATURES)


class FeatureEngine:
    async def run(
        self,
        frame: MarketFrame,
        candle_repo: CandleRepository,
        oi_repo: OpenInterestRepository,
        *,
        analyzed_at: datetime,
    ) -> SnifferFrameResult:
        # {feature_name: {instrument_id: value | None}}
        per_feature = {
            feature.name: await feature.calculate(frame, candle_repo, oi_repo)
            for feature in FEATURES
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
