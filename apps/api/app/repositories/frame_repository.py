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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

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
        trigger for the same frame_time never creates a second row (M4 section
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
        never called from the steady-state per-boundary loop.
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

    async def list_frame_times_in_range(self, start: datetime, end: datetime) -> list[datetime]:
        """Every `frame_time` that actually has a `market_frames` row in the
        half-open interval `[start, end)`, ascending — the "which frame
        times did MARKET actually finalize" read `app.research.dataset` uses to
        turn a wall-clock-aligned sampling grid into the real, existing
        frame_times to replay, and to know which aligned candidates are
        genuinely missing (see that module) rather than guessing from a
        gap-free assumption. Includes every status (BUILDING/COMPLETE/
        PARTIAL) — callers that care about status filter it themselves,
        the same way `get_frame`'s own caller does today."""
        result = await self._session.execute(
            select(MarketFrameRow.frame_time)
            .where(MarketFrameRow.frame_time >= start, MarketFrameRow.frame_time < end)
            .order_by(MarketFrameRow.frame_time)
        )
        return list(result.scalars().all())

    async def get_earliest_frame_time(self) -> datetime | None:
        """The first `frame_time` ever recorded (frame headers are never
        pruned), or None before the first frame."""
        result = await self._session.execute(select(func.min(MarketFrameRow.frame_time)))
        return result.scalar_one_or_none()

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
        multi-timeframe join over unbounded per-timeframe candle subqueries.

        That join is the single most expensive query in this service
        (measured ~3.7s locally against a ~30-day/795-instrument universe —
        ~19s before the 1m join was removed — because the planner can
        satisfy a per-timeframe subquery with a scan of every candle
        partition, there being no time bound) and is wholly unnecessary
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

    async def get_summary(self, frame_time: datetime) -> MarketFrame | None:
        """One frame's metadata (any status, `members` always `[]`) — the
        cheap read live rankings use to confirm their frame finalized."""
        row = await self._session.get(MarketFrameRow, frame_time)
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
        # The member rows first, then exactly the candles they reference,
        # looked up by key: (timeframe, open_time, instrument ids) — members
        # sharing an anchor (nearly all of them) share one indexed lookup.
        # Equivalent to joining each member to its five candles (the three
        # gating ones required, 4h/24h optional) without ever reading a
        # timeframe's whole history, which grows with every day stored.
        rows = (
            await self._session.execute(
                select(MarketFrameMemberRow, Instrument.symbol)
                .join(Instrument, Instrument.id == MarketFrameMemberRow.instrument_id)
                .where(MarketFrameMemberRow.frame_time == frame_time)
                .order_by(MarketFrameMemberRow.instrument_id)
            )
        ).all()
        references = {
            "5m": "open_time_5m",
            "15m": "open_time_15m",
            "1h": "open_time_1h",
            "4h": "open_time_4h",
            "24h": "open_time_24h",
        }
        candles: dict[tuple[str, int, datetime], Candle] = {}
        for timeframe, column in references.items():
            by_open_time: dict[datetime, list[int]] = {}
            for member_row, _ in rows:
                open_time = getattr(member_row, column)
                if open_time is not None:
                    by_open_time.setdefault(open_time, []).append(member_row.instrument_id)
            for open_time, ids in by_open_time.items():
                result = await self._session.execute(
                    select(Candle).where(
                        Candle.timeframe == timeframe,
                        Candle.open_time == open_time,
                        Candle.instrument_id.in_(ids),
                    )
                )
                for candle in result.scalars():
                    candles[(timeframe, candle.instrument_id, candle.open_time)] = candle

        def referenced(timeframe: str, member_row: MarketFrameMemberRow) -> Candle | None:
            open_time = getattr(member_row, references[timeframe])
            if open_time is None:
                return None
            return candles.get((timeframe, member_row.instrument_id, open_time))

        members: list[MarketFrameMember] = []
        for member_row, symbol in rows:
            c5, c15, ch1 = (referenced(tf, member_row) for tf in ("5m", "15m", "1h"))
            if c5 is None or c15 is None or ch1 is None:
                continue  # a gating candle no longer exists (e.g. pruned): no member, as before
            ch4, ch24 = referenced("4h", member_row), referenced("24h", member_row)
            members.append(
                MarketFrameMember(
                    instrument_id=member_row.instrument_id,
                    symbol=symbol,
                    m5=_to_context(c5),
                    m15=_to_context(c15),
                    h1=_to_context(ch1),
                    h4=_to_context(ch4) if ch4 is not None else None,
                    h24=_to_context(ch24) if ch24 is not None else None,
                )
            )
        return members
