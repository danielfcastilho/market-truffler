"""MARKET FRAME production: the temporal synchronizer that turns
`market_candles` (what happened) into `market_frames`/`market_frame_members`
(what was knowable about the whole market at a given minute).

A third long-lived background capability, started/stopped from the FastAPI
lifespan exactly like `MarketCollector` and `HistoryReconciler`. It is a
pure consumer of already-persisted candle data — it never talks to Bybit
directly.

FRAME TIME
    Market Frames occur on UTC minute boundaries. `frame_time` = M means
    "the decision point immediately after the M-1..M minute has closed":
    every candle a frame may reference has `close_time <= frame_time`.
    Since every timeframe's `close_time` is exactly
    `open_time + duration - 1 tick`, that condition is equivalent to (and
    implemented as) `open_time <= frame_time - duration` — a comparison
    that only needs the existing `(instrument_id, timeframe, open_time)`
    index, never `close_time` itself.

LIFECYCLE (per minute)
    1. Wake exactly at a UTC minute boundary M.
    2. Snapshot the active universe *now* — this fixes `expected_instruments`
       immutably; later universe changes never retroactively alter an
       already-built frame (M4 section 6).
    3. Persist a BUILDING row for frame_time=M immediately (idempotent) —
       crash-safe, inspectable state even mid-build (M4 section 11/19).
    4. Wait `grace_period_seconds` for normal live delivery to settle
       (M4 section 12) — does not finalize on the first symbol to arrive.
    5. For each of the four timeframes, one set-oriented query resolves the
       latest legal candle per instrument across the *entire* snapshotted
       universe (`CandleRepository.fetch_latest_closed_per_instrument`) —
       never a per-instrument loop (M4 section 25).
    6. An instrument is "available" only if all four timeframes resolved;
       its four `open_time`s become one member row. Missing even one
       timeframe means no member row — never a partial/fabricated one (M4
       section 8/13/14).
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

from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.instrument_repository import InstrumentRepository

logger = logging.getLogger(__name__)

# (timeframe label, duration in minutes) — the four configured frame
# timeframes. Fixed, not configurable: this is MARKET's contract with
# future Sniffer, not an operational tuning knob.
TIMEFRAMES: tuple[tuple[str, int], ...] = (("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60))

SessionFactory = Callable[[], AsyncSession]
OnFrameFinalized = Callable[[datetime], Awaitable[None]]


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


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
        single interrupted frame, never fabricates the missed minutes in
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

    # -- steady-state per-minute loop ----------------------------------------------

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            if await self._sleep_until_next_minute_boundary():
                break

            frame_time = _floor_to_minute(datetime.now(UTC))
            try:
                await self._build_frame(frame_time)
            except Exception:  # noqa: BLE001 - one bad frame must not kill the loop
                logger.exception("frame_build_failed", extra={"frame_time": frame_time.isoformat()})

    async def _sleep_until_next_minute_boundary(self) -> bool:
        """Sleep until the next :00 boundary. Returns True if stopped first."""
        assert self._stop_event is not None
        now = datetime.now(UTC)
        next_boundary = _floor_to_minute(now) + timedelta(minutes=1)
        seconds = (next_boundary - now).total_seconds()
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
            for timeframe, duration_minutes in TIMEFRAMES:
                max_open_time = frame_time - timedelta(minutes=duration_minutes)
                per_timeframe[timeframe] = await candle_repo.fetch_latest_closed_per_instrument(
                    timeframe, instrument_ids, max_open_time
                )

            now = datetime.now(UTC)
            member_rows = []
            for instrument_id in instrument_ids:
                m1 = per_timeframe["1m"].get(instrument_id)
                m5 = per_timeframe["5m"].get(instrument_id)
                m15 = per_timeframe["15m"].get(instrument_id)
                h1 = per_timeframe["1h"].get(instrument_id)
                if m1 is None or m5 is None or m15 is None or h1 is None:
                    continue  # missing even one timeframe — not available, no member row
                member_rows.append(
                    {
                        "frame_time": frame_time,
                        "instrument_id": instrument_id,
                        "open_time_1m": m1.open_time,
                        "open_time_5m": m5.open_time,
                        "open_time_15m": m15.open_time,
                        "open_time_1h": h1.open_time,
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
