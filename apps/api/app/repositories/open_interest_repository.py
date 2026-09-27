"""Persistence for Open Interest observations.

Same idempotent-upsert, set-oriented-batched-query conventions as
`CandleRepository` — see that module for the reasoning this mirrors.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.open_interest import OpenInterestObservation

_CONFLICT_COLUMNS = ("instrument_id", "observed_at")


class OpenInterestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> None:
        """Batch-upsert OI observation rows in one statement. Does nothing
        for an empty batch. See `CandleRepository.upsert_many` for why
        `expire_all()` follows the commit."""
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        stmt = insert_fn(OpenInterestObservation).values(list(rows))
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_CONFLICT_COLUMNS),
            set_={"open_interest": stmt.excluded.open_interest},
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()

    async def fetch_exact_observed_at(
        self, instrument_ids: Sequence[int], observed_at: datetime
    ) -> dict[int, OpenInterestObservation]:
        """The observation at exactly `observed_at`, for each of
        `instrument_ids` that has one — one set-oriented query, never a
        per-instrument loop. Mirrors `CandleRepository.fetch_exact_open_time`:
        an instrument absent from the result genuinely has no observation
        at that exact instant — callers must treat that as unavailable,
        never substitute the nearest one."""
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(OpenInterestObservation).where(
                OpenInterestObservation.instrument_id.in_(instrument_ids),
                OpenInterestObservation.observed_at == observed_at,
            )
        )
        return {obs.instrument_id: obs for obs in result.scalars()}

    async def fetch_latest_at_or_before_per_instrument(
        self, instrument_ids: Sequence[int], max_observed_at: datetime
    ) -> dict[int, OpenInterestObservation]:
        """The single latest observation per instrument, at or before
        `max_observed_at` — the OI equivalent of
        `CandleRepository.fetch_latest_closed_per_instrument`, used to
        pick a frame-legal "current OI" anchor (no-lookahead: never an
        observation from after `max_observed_at`)."""
        if not instrument_ids:
            return {}

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
