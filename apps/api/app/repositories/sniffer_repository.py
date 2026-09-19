"""Persistence for 🐽 SNIFFER's feature measurements.

Read/write access to `sniffer_results` — see `app.domain.sniffer` for the
shapes this returns and `app.services.sniffer` for orchestration.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.instrument import Instrument
from app.models.sniffer import SnifferResult


class SnifferRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_result(self, result: SnifferFrameResult) -> None:
        """Batch-upsert every (instrument, metric) value in `result` in one
        statement. Idempotent: re-analyzing the same frame overwrites the
        same rows rather than duplicating them (M5 section 15) — the
        composite primary key `(frame_time, instrument_id, metric)` is the
        conflict target.
        """
        rows = [
            {
                "frame_time": result.frame_time,
                "instrument_id": instrument.instrument_id,
                "metric": metric,
                "value": value,
                "calculated_at": result.analyzed_at,
            }
            for instrument in result.instruments
            for metric, value in instrument.features.items()
        ]
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        stmt = insert_fn(SnifferResult).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["frame_time", "instrument_id", "metric"],
            set_={"value": stmt.excluded.value, "calculated_at": stmt.excluded.calculated_at},
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_for_frame(self, frame_time: datetime) -> SnifferFrameResult | None:
        """Sniffer's results for one specific frame, or None if it was
        never analyzed."""
        return await self._load(frame_time)

    async def get_latest(self) -> SnifferFrameResult | None:
        """Sniffer's results for the most recently analyzed frame — the
        dominant read for Vitals/the Sniffer UI."""
        latest_frame_time = (
            await self._session.execute(select(func.max(SnifferResult.frame_time)))
        ).scalar_one_or_none()
        if latest_frame_time is None:
            return None
        return await self._load(latest_frame_time)

    async def _load(self, frame_time: datetime) -> SnifferFrameResult | None:
        stmt = (
            select(SnifferResult, Instrument.symbol)
            .join(Instrument, Instrument.id == SnifferResult.instrument_id)
            .where(SnifferResult.frame_time == frame_time)
            .order_by(Instrument.symbol)
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return None

        by_instrument: dict[int, SnifferInstrumentResult] = {}
        analyzed_at: datetime = rows[0][0].calculated_at
        for row, symbol in rows:
            existing = by_instrument.get(row.instrument_id)
            if existing is None:
                by_instrument[row.instrument_id] = SnifferInstrumentResult(
                    instrument_id=row.instrument_id, symbol=symbol, features={row.metric: row.value}
                )
            else:
                existing.features[row.metric] = row.value

        return SnifferFrameResult(
            frame_time=frame_time,
            analyzed_at=analyzed_at,
            instruments=list(by_instrument.values()),
        )
