"""MARKET FRAME production: the temporal synchronizer that turns
`market_candles` (what happened) into `market_frames`/`market_frame_members`
(what was knowable about the whole market at a given 5-minute boundary).

A third long-lived background capability, started/stopped from the FastAPI
lifespan exactly like `MarketCollector` and `HistoryReconciler`. It is a
pure consumer of already-persisted candle data — it never talks to Bybit
directly.

FRAME TIME
    Market Frames occur on UTC 5-minute boundaries — the base timeframe's
    own boundaries (see `app.domain.timeframes`), so every frame lines up
    with exactly one just-closed 5m candle and there are no frames in
    between whose "current" 5m candle would simply repeat the previous
    frame's. `frame_time` = T means "the decision point immediately after
    the 5m candle [T-5m, T) has closed": every candle a frame may
    reference has `close_time < frame_time`. Since every timeframe's
    `close_time` is just before `open_time + duration`, that condition is
    equivalent to (and implemented as) `open_time <= frame_time -
    duration` — a comparison that only needs the existing
    `(instrument_id, timeframe, open_time)` index, never `close_time`
    itself. The 5m candle opened at T itself — still forming at T — is
    never selectable.

LIFECYCLE (per 5-minute boundary)
    1. Wake exactly at a UTC 5-minute boundary T.
    2. Snapshot the active universe *now* — this fixes `expected_instruments`
       immutably; later universe changes never retroactively alter an
       already-built frame (M4 section 6).
    3. Persist a BUILDING row for frame_time=M immediately (idempotent) —
       crash-safe, inspectable state even mid-build (M4 section 11/19).
    4. Wait `grace_period_seconds` for normal live delivery to settle
       (M4 section 12) — does not finalize on the first symbol to arrive.
    5. For each configured timeframe — the three membership-gating ones
       (5m/15m/1h) plus 4h and 24h — one set-oriented query resolves the
       latest legal candle per instrument across the *entire* snapshotted
       universe (`CandleRepository.fetch_latest_closed_per_instrument`) —
       never a per-instrument loop (M4 section 25).
    6. An instrument is "available" only if all three gating timeframes
       resolved; its `open_time`s become one member row, plus 4h's/24h's
       `open_time` when either happens to be available too (never gating —
       see `app.domain.frame.MarketFrameMember.h4`/`h24`). Missing even one
       gating timeframe means no member row — never a partial/fabricated
       one (M4 section 8/13/14).
    7. `FrameRepository.finalize` batch-inserts members and flips the frame
       to COMPLETE (available == expected) or PARTIAL (available <
       expected) — truthfully, never faked (M4 section 13/14).

A finalized frame is never revisited: corrections/recovery that land in
`market_candles` later do not reopen or rewrite it (M4 section 15/16).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.timeframes import BASE_DURATION, TIMEFRAMES, floor_to_boundary
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.instrument_repository import InstrumentRepository

logger = logging.getLogger(__name__)

# Every stored timeframe is resolved into each frame (fixed, not
# configurable: this is MARKET's contract with Sniffer, not an operational
# tuning knob). 5m/15m/1h gate member availability; 4h and 24h are resolved
# the same way but do not gate it (see
# `app.domain.frame.MarketFrameMember.h4`/`h24`).
GATING_TIMEFRAMES = ("5m", "15m", "1h")

#: Longest single wait between wall-clock re-checks while sleeping to the
#: next boundary — bounds how late a frame can start after the host wakes.
WAIT_CHUNK = timedelta(seconds=5)

SessionFactory = Callable[[], AsyncSession]
OnFrameFinalized = Callable[[datetime], Awaitable[None]]


class FrameSynchronizer:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        grace_period_seconds: float = 5.0,
        on_frame_finalized: OnFrameFinalized | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._grace_period_seconds = grace_period_seconds
        self._on_frame_finalized = on_frame_finalized

        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        #: The last boundary handed to `_build_frame` — never scheduled twice.
        self._last_scheduled: datetime | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._stop_event = asyncio.Event()

        await self._resolve_interrupted_build()

        self._task = asyncio.create_task(self._run_loop())
        self._running = True
        logger.info(
            "frame_synchronizer_started", extra={"grace_period_seconds": self._grace_period_seconds}
        )

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._running = False
        logger.info("frame_synchronizer_stopped")

    # -- startup recovery ---------------------------------------------------------

    async def _resolve_interrupted_build(self) -> None:
        """If a previous process crashed between creating a BUILDING row and
        finalizing it, finalize it now rather than leaving it ambiguous
        forever (M4 section 19). Safe at any delay: the temporal query is
        bounded by frame_time regardless of when it actually runs, so a
        late finalize introduces no look-ahead.

        Downtime itself is never backfilled — this only ever resolves the
        single interrupted frame, never fabricates the missed frames in
        between (M4 section 19).
        """
        async with self._session_factory() as session:
            building = await FrameRepository(session).find_building()
        if building is None:
            return

        logger.info(
            "frame_synchronizer_resolving_interrupted_build",
            extra={"frame_time": building.frame_time.isoformat()},
        )
        await self._finalize_frame(building.frame_time)

    # -- steady-state per-boundary loop ---------------------------------------------

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            frame_time = await self._sleep_until_next_boundary()
            if frame_time is None:
                break

            try:
                await self._build_frame(frame_time)
            except Exception:  # noqa: BLE001 - one bad frame must not kill the loop
                logger.exception("frame_build_failed", extra={"frame_time": frame_time.isoformat()})

    async def _sleep_until_next_boundary(self) -> datetime | None:
        """Sleep until the next UTC 5-minute boundary and return the
        frame_time to build — or None if stopped first.

        The wait happens in short chunks (`WAIT_CHUNK`), re-reading the wall
        clock after each. A single long timer is measured on the event
        loop's monotonic clock, which stops while the host sleeps (e.g. a
        laptop lid closed with Docker's VM paused): after waking, such a
        timer kept running out its remaining time and the loop overshot,
        missing boundaries even though the host was awake again.

        Never returns before a boundary has been reached (asyncio timers can
        fire a hair early — returning early would build the wrong, previous
        boundary). Once the wall clock is at or past the awaited boundary it
        returns the *latest* boundary already reached: after a host sleep
        that skips past boundaries, the missed ones are logged and never
        built — frames are only ever produced for the present, not
        backfilled — and the next frame is the most recent eligible one.
        Never returns the same boundary twice."""
        assert self._stop_event is not None
        target = floor_to_boundary(datetime.now(UTC), BASE_DURATION) + BASE_DURATION
        if self._last_scheduled is not None and target <= self._last_scheduled:
            target = self._last_scheduled + BASE_DURATION
        while True:
            now = datetime.now(UTC)
            if now >= target:
                latest = floor_to_boundary(now, BASE_DURATION)
                skipped = int((latest - target) / BASE_DURATION)
                if skipped:
                    logger.warning(
                        "frame_boundaries_skipped",
                        extra={
                            "skipped": skipped,
                            "first_skipped": target.isoformat(),
                            "building": latest.isoformat(),
                        },
                    )
                self._last_scheduled = latest
                return latest
            remaining = min((target - now).total_seconds(), WAIT_CHUNK.total_seconds())
            if await self._wait_or_stop(remaining):
                return None

    async def _wait_or_stop(self, seconds: float) -> bool:
        """Wait up to `seconds`; True if stopped meanwhile."""
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _build_frame(self, frame_time: datetime) -> None:
        async with self._session_factory() as session:
            active = await InstrumentRepository(session).list_active()
            instrument_ids = [i.id for i in active]
            await FrameRepository(session).create_building(
                frame_time, expected_instrument_ids=instrument_ids, now=datetime.now(UTC)
            )

        if await self._sleep(self._grace_period_seconds):
            return  # stopping — leave this frame BUILDING; resolved on next startup

        await self._finalize_frame(frame_time)

    async def _sleep(self, seconds: float) -> bool:
        """Sleep for `seconds`, but wake early (returning True) if stopped."""
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return False
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _finalize_frame(self, frame_time: datetime) -> None:
        async with self._session_factory() as session:
            frame_repo = FrameRepository(session)
            # Always the snapshot taken when this frame entered BUILDING —
            # never a fresh "active instruments now" query. This is what
            # makes finalization after a crash/restart evaluate exactly the
            # same universe it started with, regardless of any universe
            # change in between (M4 follow-up).
            instrument_ids = await frame_repo.get_expected_instrument_ids(frame_time)
            if instrument_ids is None:
                logger.warning(
                    "frame_finalize_skipped_no_building_row",
                    extra={"frame_time": frame_time.isoformat()},
                )
                return

            candle_repo = CandleRepository(session)
            per_timeframe: dict[str, dict[int, Candle]] = {}
            for timeframe, duration in TIMEFRAMES.items():
                max_open_time = frame_time - duration
                per_timeframe[timeframe] = await candle_repo.fetch_latest_closed_per_instrument(
                    timeframe, instrument_ids, max_open_time
                )

            now = datetime.now(UTC)
            member_rows = []
            for instrument_id in instrument_ids:
                if any(instrument_id not in per_timeframe[tf] for tf in GATING_TIMEFRAMES):
                    continue  # missing even one gating timeframe — not available, no member row
                h4 = per_timeframe["4h"].get(instrument_id)
                h24 = per_timeframe["24h"].get(instrument_id)
                member_rows.append(
                    {
                        "frame_time": frame_time,
                        "instrument_id": instrument_id,
                        "open_time_5m": per_timeframe["5m"][instrument_id].open_time,
                        "open_time_15m": per_timeframe["15m"][instrument_id].open_time,
                        "open_time_1h": per_timeframe["1h"][instrument_id].open_time,
                        # Not gating (see MarketFrameMember.h4/h24) —
                        # legitimately None when this instrument's first
                        # 4h/24h bucket hasn't closed yet, never fabricated.
                        "open_time_4h": h4.open_time if h4 is not None else None,
                        "open_time_24h": h24.open_time if h24 is not None else None,
                    }
                )

            finalized = await frame_repo.finalize(frame_time, member_rows, now=now)

        if finalized:
            logger.info(
                "frame_finalized",
                extra={
                    "frame_time": frame_time.isoformat(),
                    "expected": len(instrument_ids),
                    "available": len(member_rows),
                },
            )
            if self._on_frame_finalized is not None:
                # Belt and suspenders: the hook (Sniffer's) is documented to
                # swallow its own errors, but MARKET's own lifecycle must
                # stay safe even if some future hook doesn't (M5 section 14).
                try:
                    await self._on_frame_finalized(frame_time)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "frame_finalized_hook_failed", extra={"frame_time": frame_time.isoformat()}
                    )
