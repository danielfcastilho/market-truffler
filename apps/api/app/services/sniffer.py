"""🐽 SNIFFER: turns a finalized Market Frame into feature measurements.

Pure orchestration — the feature formulas live in `app.features`, not
here, and this module knows nothing about HTTP, the frontend or Vitals. It
is invoked reactively, once per finalized frame, via the optional hook
`FrameSynchronizer` calls after finalizing (see `app.main`'s lifespan) —
never a polling loop, and never triggered by a request (M5 section 14).
Its only background work is one bounded recovery pass at startup
(`start_recovery`).

DECISION TIME
    A frame is measured at its decision time (`app.domain.temporal`), never
    earlier: if the hook fires first, analysis waits. The OI and funding
    inputs are filtered to what was stored by then, so a measurement does
    not depend on how quickly analysis happened to run, and a replay or a
    recovery of the same frame selects the same inputs.

RECOVERY
    An analysis interrupted by a restart used to be lost for good (audit
    F-02). On startup, `recover_missing` analyzes, oldest first, the
    finalized frames of the last `RECOVERY_LOOKBACK` that have no result —
    bounded by `RECOVERY_LIMIT`, idempotent, skipping analyzed frames, and
    labelled `analysis="recovery"` so a recovered result is never mistaken
    for a live one. Its values are what the temporal contract allows at the
    frame's decision time; for OI/funding rows stored before `ingested_at`
    was recorded, that availability is unverified (see
    docs/TEMPORAL_CONTRACT.md).

Sniffer only ever analyzes `frame.members` — the actual persisted Market
Frame membership. An instrument missing from frame T (PARTIAL frame) is
never analyzed for T, even if `market_candles` has since been repaired by
M3 (M5 section 8): Sniffer answers "what could have been known at T", not
"what do we know now about T".
"""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus
from app.domain.temporal import decision_time
from app.features.engine import FeatureEngine
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.repositories.sniffer_repository import SnifferRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleep = Callable[[float], Awaitable[object]]
#: Called with a frame time right after its analysis is committed — the
#: ranking publication boundary (`app.services.ranking_snapshots`).
Publish = Callable[[datetime], Awaitable[object]]

#: How far back startup recovery looks for frames whose analysis was lost.
RECOVERY_LOOKBACK = timedelta(hours=24)
#: Most frames one startup recovers (each takes about as long as a live
#: analysis) — a longer outage's older frames stay unanalyzed, visibly.
RECOVERY_LIMIT = 24

LIVE = "live"
RECOVERY = "recovery"


class Sniffer:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        sleep: Sleep = asyncio.sleep,
        publish: Publish | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = FeatureEngine()
        self._sleep = sleep
        self._publish = publish
        self._recovery_task: asyncio.Task[list[datetime]] | None = None

    async def analyze_frame(self, frame_time: datetime, *, analysis: str = LIVE) -> None:
        """Analyze one finalized frame and persist the results.

        A no-op (logged, not raised) if the frame doesn't exist or is still
        BUILDING — Sniffer only ever analyzes what M4 has actually
        finalized. Safe to call more than once for the same frame_time:
        `SnifferRepository.save_result` upserts idempotently (M5 section
        15). Never measures before the frame's decision time (waits).
        """
        wait = (decision_time(frame_time) - datetime.now(UTC)).total_seconds()
        if wait > 0:
            await self._sleep(wait)

        async with self._session_factory() as session:
            frame = await FrameRepository(session).get_frame(frame_time)
            if frame is None or frame.status == FrameStatus.BUILDING:
                logger.info(
                    "sniffer_skipped_unfinalized_frame",
                    extra={"frame_time": frame_time.isoformat()},
                )
                return

            candle_repo = CandleRepository(session)
            oi_repo = OpenInterestRepository(session)
            funding_repo = FundingRateRepository(session)
            result = await self._engine.run(
                frame, candle_repo, oi_repo, funding_repo, analyzed_at=datetime.now(UTC)
            )
            result = dataclasses.replace(result, analysis=analysis)
            await SnifferRepository(session).save_result(result)

        logger.info(
            "sniffer_analyzed_frame",
            extra={
                "frame_time": frame_time.isoformat(),
                "instruments": len(result.instruments),
                "analysis": analysis,
            },
        )
        if self._publish is not None:
            # The ranking of this frame is now available: capture it. A
            # capture failure never affects the analysis (it is logged and
            # counted by the publisher, never reported as captured).
            try:
                await self._publish(frame_time)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "ranking_snapshot_capture_failed", extra={"frame_time": frame_time.isoformat()}
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

    # -- startup recovery ------------------------------------------------------------

    async def recover_missing(
        self, *, now: datetime | None = None, limit: int = RECOVERY_LIMIT
    ) -> list[datetime]:
        """Analyze the finalized frames of the last `RECOVERY_LOOKBACK` (up
        to `now`) that have no result, oldest first, at most `limit`.
        Returns the frame times recovered. One failing frame is logged and
        skipped, never retried in a loop."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            missing = await SnifferRepository(session).find_unanalyzed_frames(
                now - RECOVERY_LOOKBACK, now, limit=limit
            )
        recovered = []
        for frame_time in missing:
            try:
                await self.analyze_frame(frame_time, analysis=RECOVERY)
            except Exception:  # noqa: BLE001 - one bad frame must not stop recovery
                logger.exception(
                    "sniffer_recovery_failed", extra={"frame_time": frame_time.isoformat()}
                )
                continue
            recovered.append(frame_time)
        if missing:
            logger.info(
                "sniffer_recovery_done",
                extra={"missing": len(missing), "recovered": len(recovered)},
            )
        return recovered

    async def start_recovery(self) -> None:
        """Run `recover_missing` once in the background (startup), so the
        API is serving while it works. Only frames already finalized when it
        starts are considered: frames this process finalizes are analyzed by
        the live hook."""
        self._recovery_task = asyncio.create_task(self._recover_safely(datetime.now(UTC)))

    async def _recover_safely(self, now: datetime) -> list[datetime]:
        try:
            return await self.recover_missing(now=now)
        except Exception:  # noqa: BLE001 - recovery must never break startup
            logger.exception("sniffer_recovery_failed_to_run")
            return []

    async def stop(self) -> None:
        """Cancel an unfinished recovery; whatever it did not reach is
        found again on the next startup."""
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task
            self._recovery_task = None
