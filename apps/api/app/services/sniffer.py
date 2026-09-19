"""🐽 SNIFFER: turns a finalized Market Frame into feature measurements.

Pure orchestration — the feature formulas live in `app.features`, not
here, and this module knows nothing about HTTP, the frontend, Vitals, or
application startup. It has no lifecycle of its own (no background loop):
it is invoked reactively, once per finalized frame, via the optional hook
`FrameSynchronizer` calls after finalizing (see `app.main`'s lifespan) —
never a polling loop, and never triggered by a request (M5 section 14).

Sniffer only ever analyzes `frame.members` — the actual persisted Market
Frame membership. An instrument missing from frame T (PARTIAL frame) is
never analyzed for T, even if `market_candles` has since been repaired by
M3 (M5 section 8): Sniffer answers "what could have been known at T", not
"what do we know now about T".
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus
from app.features.engine import FeatureEngine
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.sniffer_repository import SnifferRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


class Sniffer:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._engine = FeatureEngine()

    async def analyze_frame(self, frame_time: datetime) -> None:
        """Analyze one finalized frame and persist the results.

        A no-op (logged, not raised) if the frame doesn't exist or is still
        BUILDING — Sniffer only ever analyzes what M4 has actually
        finalized. Safe to call more than once for the same frame_time:
        `SnifferRepository.save_result` upserts idempotently (M5 section
        15).
        """
        async with self._session_factory() as session:
            frame = await FrameRepository(session).get_frame(frame_time)
            if frame is None or frame.status == FrameStatus.BUILDING:
                logger.info(
                    "sniffer_skipped_unfinalized_frame",
                    extra={"frame_time": frame_time.isoformat()},
                )
                return

            candle_repo = CandleRepository(session)
            result = await self._engine.run(frame, candle_repo, analyzed_at=datetime.now(UTC))
            await SnifferRepository(session).save_result(result)

        logger.info(
            "sniffer_analyzed_frame",
            extra={"frame_time": frame_time.isoformat(), "instruments": len(result.instruments)},
        )

    async def on_frame_finalized(self, frame_time: datetime) -> None:
        """The hook wired into `FrameSynchronizer` — see `app.main`. Any
        failure here is swallowed (logged only): a Sniffer bug or a
        transient DB hiccup must never break MARKET's own lifecycle (M5
        section 14).
        """
        try:
            await self.analyze_frame(frame_time)
        except Exception:  # noqa: BLE001 - Sniffer must never break MARKET
            logger.exception(
                "sniffer_analysis_failed", extra={"frame_time": frame_time.isoformat()}
            )
