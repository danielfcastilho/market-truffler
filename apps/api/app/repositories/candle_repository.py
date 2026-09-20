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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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

    async def fetch_latest_closed_per_instrument(
        self, timeframe: str, instrument_ids: Sequence[int], max_open_time: datetime
    ) -> dict[int, Candle]:
        """The single latest legally-closed candle per instrument, for one
        timeframe, as of `max_open_time` (inclusive) — one set-oriented
        query for the whole given instrument set, never a per-instrument
        loop.

        `max_open_time` is expected to already encode the timeframe's
        duration (e.g. `frame_time - 1h` for the "1h" timeframe), so a
        plain `open_time <= max_open_time` comparison is exactly equivalent
        to "this candle's close_time was <= frame_time" without needing an
        index on `close_time` at all — the composite primary key
        `(instrument_id, timeframe, open_time)` serves this directly.

        Implemented with a portable `ROW_NUMBER() OVER (PARTITION BY
        instrument_id ORDER BY open_time DESC)` window function (standard
        SQL, not PostgreSQL-specific `DISTINCT ON`) so it runs identically
        against SQLite in tests and PostgreSQL in production.
        """
        if not instrument_ids:
            return {}

        row_number = (
            func.row_number()
            .over(partition_by=Candle.instrument_id, order_by=Candle.open_time.desc())
            .label("rn")
        )
        ranked = (
            select(Candle, row_number)
            .where(
                Candle.timeframe == timeframe,
                Candle.instrument_id.in_(instrument_ids),
                Candle.open_time <= max_open_time,
            )
            .subquery()
        )
        # Re-map the subquery's columns back onto the Candle entity (rather
        # than leaving plain Row tuples) so callers use the same `.open_time`
        # / `.close` / etc. attribute access as every other repository
        # method here.
        ranked_candle = aliased(Candle, ranked)
        latest = select(ranked_candle).where(ranked.c.rn == 1)

        result = await self._session.execute(latest)
        return {candle.instrument_id: candle for candle in result.scalars()}

    async def fetch_latest_n_closed_per_instrument(
        self, timeframe: str, instrument_ids: Sequence[int], max_open_time: datetime, n: int
    ) -> dict[int, list[Candle]]:
        """The `n` most recent legally-closed candles per instrument, for one
        timeframe, as of `max_open_time` (inclusive) — one set-oriented query
        for the whole given instrument set, never a per-instrument loop (used
        by `app.features.rsi` for RSI's fixed-length lookback window).

        Returns each instrument's candles ordered oldest-to-newest. An
        instrument with fewer than `n` legally-closed candles as of
        `max_open_time` simply gets a shorter (or empty) list — callers must
        treat that as insufficient history, never pad or substitute.

        Same `ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY
        open_time DESC)` technique as `fetch_latest_closed_per_instrument`,
        generalized from `rn == 1` to `rn <= n`.
        """
        if not instrument_ids or n <= 0:
            return {}

        row_number = (
            func.row_number()
            .over(partition_by=Candle.instrument_id, order_by=Candle.open_time.desc())
            .label("rn")
        )
        ranked = (
            select(Candle, row_number)
            .where(
                Candle.timeframe == timeframe,
                Candle.instrument_id.in_(instrument_ids),
                Candle.open_time <= max_open_time,
            )
            .subquery()
        )
        ranked_candle = aliased(Candle, ranked)
        latest_n = select(ranked_candle).where(ranked.c.rn <= n)

        result = await self._session.execute(latest_n)
        grouped: dict[int, list[Candle]] = {}
        for candle in result.scalars():
            grouped.setdefault(candle.instrument_id, []).append(candle)
        for candles in grouped.values():
            candles.sort(key=lambda c: c.open_time)
        return grouped

    async def fetch_exact_open_time(
        self, timeframe: str, instrument_ids: Sequence[int], open_time: datetime
    ) -> dict[int, Candle]:
        """The candle at exactly `open_time`, for each of `instrument_ids`
        that has one — one set-oriented query for the whole given
        instrument set, never a per-instrument loop.

        Used for frame-relative, time-based historical lookups (e.g.
        Sniffer's `return_5m`: "the canonical 1m candle exactly 5 minutes
        before the current one", never "the Nth previous row"). An
        instrument absent from the result genuinely has no candle at that
        exact instant — callers must treat that as unavailable, never
        substitute the nearest one.
        """
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(Candle).where(
                Candle.timeframe == timeframe,
                Candle.instrument_id.in_(instrument_ids),
                Candle.open_time == open_time,
            )
        )
        return {candle.instrument_id: candle for candle in result.scalars()}
