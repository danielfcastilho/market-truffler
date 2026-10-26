"""Batched, idempotent candle persistence.

Every write is an `INSERT ... ON CONFLICT (instrument_id, timeframe,
open_time) DO UPDATE` — the same canonical candle arriving twice (live
WebSocket, REST bootstrap, REST recovery, a retry) is harmless, and an
exchange-backed correction to an already-stored candle simply overwrites
it, consistent with Bybit's data being authoritative.

Upsert syntax differs by dialect (PostgreSQL in production, SQLite in
tests — see `app.db.session`), so this is the one place that branches on
it; every caller gets one dialect-agnostic API.

Reads are bounded in time wherever the answer allows it: candles are kept
for months, and a query that ranks a coin's whole history to keep its
latest few rows costs seconds per frame and grows with every day stored.
`cached()` gives a per-computation view (one `FeatureEngine.run`) that
answers a repeated window read from memory instead of the database.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.timeframes import TIMEFRAMES
from app.models.candle import Candle

_CONFLICT_COLUMNS = ("instrument_id", "timeframe", "open_time")
_UPDATE_COLUMNS = ("close_time", "open", "high", "low", "close", "volume", "turnover")


#: How far back the first, bounded phase of a "latest closed candle" read
#: looks, in candles of the timeframe; anything older is only searched for
#: the instruments the bounded phase found nothing for.
RECENT_CANDLES = 48


class CandleRepository:
    def __init__(self, session: AsyncSession, *, _cache: dict[Any, Any] | None = None) -> None:
        self._session = session
        self._cache = _cache

    def cached(self) -> "CandleRepository":
        """The same repository, but repeated `fetch_window_per_instrument`
        calls with identical arguments are answered from memory. For one
        point-in-time computation only (a `FeatureEngine.run`): never for a
        repository that also writes or lives across frames."""
        return CandleRepository(self._session, _cache={})

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
        self,
        timeframe: str,
        instrument_ids: Sequence[int],
        max_open_time: datetime,
        *,
        min_open_time: datetime | None = None,
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

        `min_open_time` (inclusive) ignores older candles — research
        reconstruction only (`app.research.reconstruction`); live frames
        pass none. Without it the read runs in two phases with an
        identical result: the last `RECENT_CANDLES` candles first, then the
        full history only for instruments that had none in that range.
        """
        if not instrument_ids:
            return {}
        duration = TIMEFRAMES.get(timeframe)
        if min_open_time is None and duration is not None:
            recent = await self._latest_closed(
                timeframe, instrument_ids, max_open_time, max_open_time - duration * RECENT_CANDLES
            )
            rest = [i for i in instrument_ids if i not in recent]
            if rest:
                recent.update(await self._latest_closed(timeframe, rest, max_open_time, None))
            return recent
        return await self._latest_closed(timeframe, instrument_ids, max_open_time, min_open_time)

    async def _latest_closed(
        self,
        timeframe: str,
        instrument_ids: Sequence[int],
        max_open_time: datetime,
        min_open_time: datetime | None,
    ) -> dict[int, Candle]:
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
                *([Candle.open_time >= min_open_time] if min_open_time is not None else []),
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

    async def fetch_window_per_instrument(
        self, timeframe: str, instrument_ids: Sequence[int], anchor_time: datetime, n: int
    ) -> dict[int, list[Candle]]:
        """Each instrument's candles of one timeframe opened within the `n`
        bars ending at `anchor_time` (`anchor_time - (n-1) × duration` to
        `anchor_time`, inclusive), oldest first — one set-oriented range
        read, never a per-instrument loop. Used for the fixed-length
        lookbacks of `app.features.rsi` and `app.features.candle_window`.

        No gap-filling: an instrument with missing bars gets fewer (or no)
        candles, and callers accept a window only when it is exactly the
        `n` consecutive bars ending at the anchor — so reading only that
        range returns everything a valid window can contain.
        """
        if not instrument_ids or n <= 0:
            return {}
        key = (timeframe, frozenset(instrument_ids), anchor_time, n)
        if self._cache is not None and key in self._cache:
            return self._cache[key]  # type: ignore[no-any-return]
        duration = TIMEFRAMES[timeframe]
        result = await self._session.execute(
            select(Candle)
            .where(
                Candle.timeframe == timeframe,
                Candle.instrument_id.in_(instrument_ids),
                Candle.open_time <= anchor_time,
                Candle.open_time >= anchor_time - duration * (n - 1),
            )
            .order_by(Candle.instrument_id, Candle.open_time)
        )
        grouped: dict[int, list[Candle]] = {}
        for candle in result.scalars():
            grouped.setdefault(candle.instrument_id, []).append(candle)
        if self._cache is not None:
            self._cache[key] = grouped
        return grouped

    async def fetch_exact_open_time(
        self, timeframe: str, instrument_ids: Sequence[int], open_time: datetime
    ) -> dict[int, Candle]:
        """The candle at exactly `open_time`, for each of `instrument_ids`
        that has one — one set-oriented query for the whole given
        instrument set, never a per-instrument loop.

        Used for frame-relative, time-based historical lookups (e.g.
        Sniffer's `return_1h`: "the 5m candle exactly 1 hour before the
        current one", never "the Nth previous row"). An
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
