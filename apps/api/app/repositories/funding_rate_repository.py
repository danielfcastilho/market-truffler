"""Persistence for Funding Rate observations.

Same idempotent-upsert, set-oriented-batched-query conventions as
`OpenInterestRepository` — see that module for the reasoning this mirrors.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.funding_rate import FundingRateObservation

_CONFLICT_COLUMNS = ("instrument_id", "funding_time")


class FundingRateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> None:
        """Batch-upsert funding observation rows in one statement. Does
        nothing for an empty batch. See `CandleRepository.upsert_many` for
        why `expire_all()` follows the commit."""
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        stmt = insert_fn(FundingRateObservation).values(list(rows))
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_CONFLICT_COLUMNS),
            set_={"funding_rate": stmt.excluded.funding_rate},
        )
        await self._session.execute(stmt)
        await self._session.commit()
        self._session.expire_all()

    async def fetch_latest_at_or_before_per_instrument(
        self, instrument_ids: Sequence[int], max_funding_time: datetime
    ) -> dict[int, FundingRateObservation]:
        """The single latest funding observation per instrument, at or
        before `max_funding_time` — the funding-rate equivalent of
        `OpenInterestRepository.fetch_latest_at_or_before_per_instrument`,
        used as `funding_rate_current`'s no-look-ahead-safe anchor."""
        if not instrument_ids:
            return {}

        row_number = (
            func.row_number()
            .over(
                partition_by=FundingRateObservation.instrument_id,
                order_by=FundingRateObservation.funding_time.desc(),
            )
            .label("rn")
        )
        ranked = (
            select(FundingRateObservation, row_number)
            .where(
                FundingRateObservation.instrument_id.in_(instrument_ids),
                FundingRateObservation.funding_time <= max_funding_time,
            )
            .subquery()
        )
        ranked_obs = aliased(FundingRateObservation, ranked)
        latest = select(ranked_obs).where(ranked.c.rn == 1)

        result = await self._session.execute(latest)
        return {obs.instrument_id: obs for obs in result.scalars()}

    async def fetch_since_per_instrument(
        self, instrument_ids: Sequence[int], since: datetime, until: datetime
    ) -> dict[int, list[FundingRateObservation]]:
        """Every observation with `since <= funding_time <= until`, per
        instrument — the trailing window `funding_rate_24h_avg` averages
        over. One set-oriented query for the whole member list, never a
        per-instrument loop. An instrument with no observations in the
        window is simply absent from the result."""
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(FundingRateObservation)
            .where(
                FundingRateObservation.instrument_id.in_(instrument_ids),
                FundingRateObservation.funding_time >= since,
                FundingRateObservation.funding_time <= until,
            )
            .order_by(FundingRateObservation.funding_time)
        )
        by_instrument: dict[int, list[FundingRateObservation]] = {}
        for obs in result.scalars():
            by_instrument.setdefault(obs.instrument_id, []).append(obs)
        return by_instrument

    async def fetch_latest_funding_time_per_instrument(
        self, instrument_ids: Sequence[int]
    ) -> dict[int, datetime]:
        """Each instrument's single most recent persisted `funding_time`,
        regardless of how it relates to "now" — the resume point
        `FundingRateReconciler`'s forward catch-up fetches from. An
        instrument absent from the result has no observation persisted
        yet at all (needs a full bootstrap, not catch-up)."""
        if not instrument_ids:
            return {}

        result = await self._session.execute(
            select(
                FundingRateObservation.instrument_id,
                func.max(FundingRateObservation.funding_time),
            )
            .where(FundingRateObservation.instrument_id.in_(instrument_ids))
            .group_by(FundingRateObservation.instrument_id)
        )
        return {instrument_id: funding_time for instrument_id, funding_time in result.all()}

    async def delete_before(self, cutoff: datetime) -> None:
        """Prune every observation older than `cutoff` — funding's
        whole-table equivalent of `OpenInterestRepository.delete_before`."""
        await self._session.execute(
            delete(FundingRateObservation).where(FundingRateObservation.funding_time < cutoff)
        )
        await self._session.commit()
