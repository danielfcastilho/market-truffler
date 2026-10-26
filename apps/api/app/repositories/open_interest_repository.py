"""Persistence for Open Interest observations.

Same idempotent-upsert, set-oriented-batched-query conventions as
`CandleRepository` — see that module for the reasoning this mirrors.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.open_interest import OpenInterestObservation

_CONFLICT_COLUMNS = ("instrument_id", "observed_at")


def _available_by(available_by: datetime | None) -> list[Any]:
    """The temporal contract's availability condition (see
    `app.domain.temporal`): stored no later than `available_by`, or a legacy
    row whose availability was never recorded. No condition when
    `available_by` is None."""
    if available_by is None:
        return []
    column = OpenInterestObservation.ingested_at
    return [or_(column.is_(None), column <= available_by)]


#: The first, bounded phase of a "latest observation" read looks back this
#: far; older observations are only searched for instruments it found
#: nothing for (identical result, without ranking months of history).
RECENT_OBSERVATIONS = timedelta(days=1)


class OpenInterestRepository:
    def __init__(self, session: AsyncSession, *, _cache: dict[Any, Any] | None = None) -> None:
        self._session = session
        self._cache = _cache

    def cached(self) -> "OpenInterestRepository":
        """The same repository, but repeated
        `fetch_latest_at_or_before_per_instrument` calls with identical
        arguments are answered from memory — for one point-in-time
        computation (a `FeatureEngine.run`), whose five `oi_change_*`
        features share one "current OI" anchor."""
        return OpenInterestRepository(self._session, _cache={})

    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> None:
        """Batch-upsert OI observation rows in one statement. Does nothing
        for an empty batch. See `CandleRepository.upsert_many` for why
        `expire_all()` follows the commit."""
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        stmt = insert_fn(OpenInterestObservation).values(list(rows))
        # `ingested_at` is first-seen availability evidence: a re-fetch of
        # the same value keeps the original instant (including a legacy
        # NULL); only an exchange-side change of the value records when the
        # new value became available.
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_CONFLICT_COLUMNS),
            set_={
                "open_interest": stmt.excluded.open_interest,
                "ingested_at": case(
                    (
                        OpenInterestObservation.open_interest == stmt.excluded.open_interest,
                        OpenInterestObservation.ingested_at,
                    ),
                    else_=stmt.excluded.ingested_at,
                ),
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()

    async def fetch_exact_observed_at(
        self,
        instrument_ids: Sequence[int],
        observed_at: datetime,
        *,
        available_by: datetime | None = None,
    ) -> dict[int, OpenInterestObservation]:
        """The observation at exactly `observed_at`, for each of
        `instrument_ids` that has one — one set-oriented query, never a
        per-instrument loop. Mirrors `CandleRepository.fetch_exact_open_time`:
        an instrument absent from the result genuinely has no observation
        at that exact instant — callers must treat that as unavailable,
        never substitute the nearest one. `available_by` applies the
        temporal contract's availability condition."""
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(OpenInterestObservation).where(
                OpenInterestObservation.instrument_id.in_(instrument_ids),
                OpenInterestObservation.observed_at == observed_at,
                *_available_by(available_by),
            )
        )
        return {obs.instrument_id: obs for obs in result.scalars()}

    async def fetch_latest_at_or_before_per_instrument(
        self,
        instrument_ids: Sequence[int],
        max_observed_at: datetime,
        *,
        available_by: datetime | None = None,
    ) -> dict[int, OpenInterestObservation]:
        """The single latest observation per instrument, at or before
        `max_observed_at` — the OI equivalent of
        `CandleRepository.fetch_latest_closed_per_instrument`, used to
        pick a frame-legal "current OI" anchor (no-lookahead: never an
        observation from after `max_observed_at`). With `available_by`,
        only observations available by then count (an observation stored
        later is skipped, never waited for). Runs in two phases with an
        identical result: the last `RECENT_OBSERVATIONS` first, the full
        history only for instruments with nothing in that range."""
        if not instrument_ids:
            return {}
        key = (frozenset(instrument_ids), max_observed_at, available_by)
        if self._cache is not None and key in self._cache:
            return self._cache[key]  # type: ignore[no-any-return]
        latest = await self._latest_at_or_before(
            instrument_ids, max_observed_at, max_observed_at - RECENT_OBSERVATIONS, available_by
        )
        rest = [i for i in instrument_ids if i not in latest]
        if rest:
            older = await self._latest_at_or_before(rest, max_observed_at, None, available_by)
            latest.update(older)
        if self._cache is not None:
            self._cache[key] = latest
        return latest

    async def _latest_at_or_before(
        self,
        instrument_ids: Sequence[int],
        max_observed_at: datetime,
        min_observed_at: datetime | None,
        available_by: datetime | None,
    ) -> dict[int, OpenInterestObservation]:
        row_number = (
            func.row_number()
            .over(
                partition_by=OpenInterestObservation.instrument_id,
                order_by=OpenInterestObservation.observed_at.desc(),
            )
            .label("rn")
        )
        ranked = (
            select(OpenInterestObservation, row_number)
            .where(
                OpenInterestObservation.instrument_id.in_(instrument_ids),
                OpenInterestObservation.observed_at <= max_observed_at,
                *(
                    [OpenInterestObservation.observed_at >= min_observed_at]
                    if min_observed_at is not None
                    else []
                ),
                *_available_by(available_by),
            )
            .subquery()
        )
        ranked_obs = aliased(OpenInterestObservation, ranked)
        latest = select(ranked_obs).where(ranked.c.rn == 1)

        result = await self._session.execute(latest)
        return {obs.instrument_id: obs for obs in result.scalars()}

    async def fetch_latest_observed_at_per_instrument(
        self, instrument_ids: Sequence[int]
    ) -> dict[int, datetime]:
        """Each instrument's single most recent persisted `observed_at`,
        regardless of how it relates to "now" — the resume point
        `OpenInterestReconciler`'s forward catch-up fetches from. An
        instrument absent from the result has no observation persisted
        yet at all (needs a full bootstrap, not catch-up)."""
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(
                OpenInterestObservation.instrument_id,
                func.max(OpenInterestObservation.observed_at),
            )
            .where(OpenInterestObservation.instrument_id.in_(instrument_ids))
            .group_by(OpenInterestObservation.instrument_id)
        )
        return {instrument_id: observed_at for instrument_id, observed_at in result.all()}

    async def find_gaps(
        self, instrument_id: int, *, bucket: timedelta
    ) -> list[tuple[datetime, datetime]]:
        """Interior gaps in one instrument's stored series: pairs of
        consecutive observations further apart than one `bucket`, as
        `(last observation before the gap, first observation after it)`.
        Only gaps *between* stored observations — nothing before the first
        or after the last (that's bootstrap's and catch-up's job)."""
        query = select(OpenInterestObservation.observed_at).where(
            OpenInterestObservation.instrument_id == instrument_id
        )
        times = list((await self._session.execute(query.order_by("observed_at"))).scalars())
        return [
            (earlier, later)
            for earlier, later in zip(times, times[1:], strict=False)
            if later - earlier > bucket
        ]

    async def delete_before(self, cutoff: datetime) -> None:
        """Prune every observation older than `cutoff` — OI's whole-table
        equivalent of `partition_manager.drop_expired_partitions`, just as
        a plain DELETE rather than partition drops (see
        `OpenInterestObservation`'s docstring for why the table doesn't
        need partitioning at this data volume)."""
        await self._session.execute(
            delete(OpenInterestObservation).where(OpenInterestObservation.observed_at < cutoff)
        )
        await self._session.commit()
