"""Batched, idempotent candle persistence.

Every write is an `INSERT ... ON CONFLICT (instrument_id, timeframe,
open_time) DO UPDATE` — the same canonical candle arriving twice (live
WebSocket, REST bootstrap, REST recovery, a retry) is harmless, and an
exchange-backed correction to an already-stored candle simply overwrites
it, consistent with Bybit's data being authoritative.

Upsert syntax differs by dialect (PostgreSQL in production, SQLite in
tests — see `app.db.session`), so this is the one place that branches on
it; every caller gets one dialect-agnostic API.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle

_CONFLICT_COLUMNS = ("instrument_id", "timeframe", "open_time")
_UPDATE_COLUMNS = ("close_time", "open", "high", "low", "close", "volume", "turnover")


class CandleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> None:
        """Batch-upsert candle rows in one statement.

        Each row is a plain dict with Candle's columns (instrument_id,
        timeframe, open_time, close_time, open, high, low, close, volume,
        turnover). Does nothing for an empty batch.
        """
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        stmt = insert_fn(Candle).values(list(rows))
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_CONFLICT_COLUMNS),
            set_={col: getattr(stmt.excluded, col) for col in _UPDATE_COLUMNS},
        )
        await self._session.execute(stmt)
        await self._session.commit()
        # This session's config disables commit-time auto-expiry
        # (`expire_on_commit=False`, chosen so ORM attributes stay readable
        # post-commit without a lazy-refresh query). That means a Core-level
        # upsert like this one — which updates rows outside the ORM's unit
        # of work — leaves any already-loaded `Candle` instance for the same
        # row stale in this session's identity map. Expire explicitly so the
        # next read (e.g. aggregate derivation re-fetching the range this
        # same call just corrected) sees the true, just-written values
        # instead of a stale in-memory copy.
        self._session.expire_all()

    async def fetch_range(
        self, instrument_id: int, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """All candles for one instrument/timeframe in `[start, end)`, ordered by open_time."""
        result = await self._session.execute(
            select(Candle)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe == timeframe,
                Candle.open_time >= start,
                Candle.open_time < end,
            )
            .order_by(Candle.open_time)
        )
        return list(result.scalars().all())
