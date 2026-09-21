"""Persistence for MARKET FRAMES.

Read/write access to `market_frames`/`market_frame_members`, plus the joins
needed to turn stored `open_time` references back into full OHLCV context
from `market_candles` for the dominant "give me frame T" read — see
`app.domain.frame` for the shapes this returns and
`app.services.frame_synchronizer` for how frames are built.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.models.candle import Candle
from app.models.frame import MarketFrame as MarketFrameRow
from app.models.frame import MarketFrameMember as MarketFrameMemberRow
from app.models.instrument import Instrument


def _to_context(candle: Candle) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        turnover=candle.turnover,
    )


class FrameRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_building(
        self, frame_time: datetime, *, expected_instrument_ids: Sequence[int], now: datetime
    ) -> None:
        """Idempotently insert a BUILDING row for `frame_time`, snapshotting
        the exact active-universe instrument IDs at this instant.

        A no-op if a row already exists for this frame_time (any status) —
        never resets an already-finalized frame, and a duplicate build
        trigger for the same minute never creates a second row (M4 section
        18/11). `expected_instrument_ids` — not just a count — is what
        finalization (including after a crash/restart) actually iterates,
        so the original snapshot survives regardless of later universe
        changes (see `MarketFrame`'s docstring).
        """
        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        ids = list(expected_instrument_ids)
        stmt = insert_fn(MarketFrameRow).values(
            frame_time=frame_time,
            expected_instrument_ids=ids,
            expected_instruments=len(ids),
            available_instruments=0,
            status=FrameStatus.BUILDING.value,
            created_at=now,
            finalized_at=None,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["frame_time"])
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()

    async def get_expected_instrument_ids(self, frame_time: datetime) -> list[int] | None:
        """The exact instrument IDs snapshotted when `frame_time` entered
        BUILDING, or None if no frame row exists for it. This is what
        finalization must iterate — never a fresh "current active
        instruments" query — so a crash-and-restart finalizes against
        exactly the same universe it started with (M4 follow-up)."""
        row = (
            await self._session.execute(
                select(MarketFrameRow.expected_instrument_ids).where(
                    MarketFrameRow.frame_time == frame_time
                )
            )
        ).scalar_one_or_none()
        return row

    async def find_building(self) -> MarketFrameRow | None:
        """The frame currently in BUILDING status, if any.

        Used only at startup to deterministically resolve a build
        interrupted by a previous process's crash/restart (M4 section 19) —
        never called from the steady-state per-minute loop.
        """
        result = await self._session.execute(
            select(MarketFrameRow).where(MarketFrameRow.status == FrameStatus.BUILDING.value)
        )
        return result.scalars().first()

    async def finalize(
        self, frame_time: datetime, member_rows: Sequence[dict[str, Any]], *, now: datetime
    ) -> bool:
        """Batch-insert `member_rows` and transition BUILDING -> COMPLETE/PARTIAL.

        Guarded by `WHERE status = 'building'`, so calling this twice for
        the same frame_time is harmless: the second call updates zero rows
        and leaves the already-terminal frame exactly as it was — a
        finalized frame is never silently rewritten by late data (M4
        section 15/16). Returns whether this call actually finalized it.
        """
        if member_rows:
            dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
            insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert
            stmt = insert_fn(MarketFrameMemberRow).values(list(member_rows))
            stmt = stmt.on_conflict_do_nothing(index_elements=["frame_time", "instrument_id"])
            await self._session.execute(stmt)

        frame = (
            await self._session.execute(
                select(MarketFrameRow).where(MarketFrameRow.frame_time == frame_time)
            )
        ).scalar_one_or_none()
        if frame is None or frame.status != FrameStatus.BUILDING.value:
            await self._session.commit()
            return False

        available = len(member_rows)
        status = (
            FrameStatus.COMPLETE.value
            if available >= frame.expected_instruments
            else FrameStatus.PARTIAL.value
        )
        frame.available_instruments = available
        frame.status = status
        frame.finalized_at = now
        await self._session.commit()
        self._session.expire_all()
        return True

    async def get_frame(self, frame_time: datetime) -> MarketFrame | None:
        """Frame metadata plus every member's full joined OHLCV context —
        the "give me frame T" read. One query per timeframe join, never a
        per-instrument loop."""
        row = (
            await self._session.execute(
                select(MarketFrameRow).where(MarketFrameRow.frame_time == frame_time)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return await self._hydrate(row)

    async def get_latest_finalized(self) -> MarketFrame | None:
        """The most recently finalized (COMPLETE or PARTIAL) frame, with
        full joined per-member OHLCV context — the dominant read for
        inspection/debugging consumers (e.g. `/api/market/frames/latest`).

        Callers that only need frame-level facts (frame_time, status,
        completeness) should use `get_latest_finalized_summary` instead —
        see its docstring for why."""
        row = await self._latest_finalized_row()
        if row is None:
            return None
        return await self._hydrate(row)

    async def get_latest_finalized_summary(self) -> MarketFrame | None:
        """Same frame as `get_latest_finalized`, but frame-level metadata
        only (`members` always `[]`) — never runs `_fetch_members`'s
        five-way join over unbounded per-timeframe candle subqueries.

        That join is the single most expensive query in this service
        (measured ~16s locally against a ~30-day/771-instrument universe,
        because each of the five per-timeframe subqueries scans its whole
        timeframe's candles with no time bound) and is wholly unnecessary
        for a caller that only wants `frame_time`/`completeness` — e.g.
        Vitals' "Latest market frame"/"Frame completeness" rows, which
        must stay a cheap observation, never triggering this cost merely
        because a human opened the page.
        """
        row = await self._latest_finalized_row()
        if row is None:
            return None
        return MarketFrame(
            frame_time=row.frame_time,
            expected_instruments=row.expected_instruments,
            available_instruments=row.available_instruments,
            status=FrameStatus(row.status),
            created_at=row.created_at,
            finalized_at=row.finalized_at,
            members=[],
        )

    async def _latest_finalized_row(self) -> MarketFrameRow | None:
        result = await self._session.execute(
            select(MarketFrameRow)
            .where(MarketFrameRow.status != FrameStatus.BUILDING.value)
            .order_by(MarketFrameRow.frame_time.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _hydrate(self, row: MarketFrameRow) -> MarketFrame:
        members = await self._fetch_members(row.frame_time)
        return MarketFrame(
            frame_time=row.frame_time,
            expected_instruments=row.expected_instruments,
            available_instruments=row.available_instruments,
            status=FrameStatus(row.status),
            created_at=row.created_at,
            finalized_at=row.finalized_at,
            members=members,
        )

    async def _fetch_members(self, frame_time: datetime) -> list[MarketFrameMember]:
        # Re-derive proper mapped Candle entities from each timeframe-scoped
        # subquery (see CandleRepository.fetch_latest_closed_per_instrument
        # for why `aliased` rather than raw Row tuples), then join all five
        # to the member row plus the instrument, in one query — the
        # "give me frame T" read is always this one statement, never a
        # per-instrument loop. 4h is an OUTER join (unlike the four gating
        # timeframes): `open_time_4h` can be NULL (see
        # `MarketFrameMember.h4`), and even when set, an INNER join would be
        # just as correct — OUTER costs nothing extra and stays correct
        # either way.
        m1, m5, m15, h1, h4 = (
            aliased(Candle, select(Candle).where(Candle.timeframe == tf).subquery())
            for tf in ("1m", "5m", "15m", "1h", "4h")
        )

        stmt = (
            select(MarketFrameMemberRow, Instrument.symbol, m1, m5, m15, h1, h4)
            .join(Instrument, Instrument.id == MarketFrameMemberRow.instrument_id)
            .join(
                m1,
                (m1.instrument_id == MarketFrameMemberRow.instrument_id)
                & (m1.open_time == MarketFrameMemberRow.open_time_1m),
            )
            .join(
                m5,
                (m5.instrument_id == MarketFrameMemberRow.instrument_id)
                & (m5.open_time == MarketFrameMemberRow.open_time_5m),
            )
            .join(
                m15,
                (m15.instrument_id == MarketFrameMemberRow.instrument_id)
                & (m15.open_time == MarketFrameMemberRow.open_time_15m),
            )
            .join(
                h1,
                (h1.instrument_id == MarketFrameMemberRow.instrument_id)
                & (h1.open_time == MarketFrameMemberRow.open_time_1h),
            )
            .outerjoin(
                h4,
                (h4.instrument_id == MarketFrameMemberRow.instrument_id)
                & (h4.open_time == MarketFrameMemberRow.open_time_4h),
            )
            .where(MarketFrameMemberRow.frame_time == frame_time)
        )
        result = await self._session.execute(stmt)

        members: list[MarketFrameMember] = []
        for member_row, symbol, c1, c5, c15, ch1, ch4 in result.all():
            members.append(
                MarketFrameMember(
                    instrument_id=member_row.instrument_id,
                    symbol=symbol,
                    m1=_to_context(c1),
                    m5=_to_context(c5),
                    m15=_to_context(c15),
                    h1=_to_context(ch1),
                    h4=_to_context(ch4) if ch4 is not None else None,
                )
            )
        return members
