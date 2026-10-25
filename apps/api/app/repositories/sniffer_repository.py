"""Persistence for 🐽 SNIFFER's feature measurements.

Read/write access to `sniffer_results` — see `app.domain.sniffer` for the
shapes this returns and `app.services.sniffer` for orchestration.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus
from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.frame import MarketFrame
from app.models.instrument import Instrument
from app.models.sniffer import SnifferResult

#: Postgres caps a statement at 65535 bind parameters — 5 columns/row, so
#: this stays comfortably under that even as more features are added to
#: `app.features.engine.FEATURES` (same reasoning as
#: `app.scripts.backfill_derived_timeframes`'s `_CHUNK`).
_ROWS_PER_STATEMENT = 5000


class SnifferRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_result(self, result: SnifferFrameResult) -> None:
        """Batch-upsert every (instrument, metric) value in `result`, in
        chunks bounded to stay under Postgres's per-statement bind
        parameter limit. Idempotent: re-analyzing the same frame overwrites
        the same rows rather than duplicating them (M5 section 15) — the
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
                "analysis": result.analysis,
            }
            for instrument in result.instruments
            for metric, value in instrument.features.items()
        ]
        if not rows:
            return

        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

        for start in range(0, len(rows), _ROWS_PER_STATEMENT):
            chunk = rows[start : start + _ROWS_PER_STATEMENT]
            stmt = insert_fn(SnifferResult).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["frame_time", "instrument_id", "metric"],
                set_={
                    "value": stmt.excluded.value,
                    "calculated_at": stmt.excluded.calculated_at,
                    "analysis": stmt.excluded.analysis,
                },
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

    def _unanalyzed(self, since: datetime, until: datetime) -> Select[tuple[datetime]]:
        """Finalized frames in `[since, until]` with members but no Sniffer
        result at all — an analysis that was lost (e.g. a restart while it
        ran). A frame with no members legitimately has no result."""
        return (
            select(MarketFrame.frame_time)
            .where(
                MarketFrame.frame_time >= since,
                MarketFrame.frame_time <= until,
                MarketFrame.status != FrameStatus.BUILDING.value,
                MarketFrame.available_instruments > 0,
                ~exists().where(SnifferResult.frame_time == MarketFrame.frame_time),
            )
            .order_by(MarketFrame.frame_time)
        )

    async def find_unanalyzed_frames(
        self, since: datetime, until: datetime, *, limit: int
    ) -> list[datetime]:
        """Up to `limit` unanalyzed frame times in `[since, until]`, oldest
        first."""
        stmt = self._unanalyzed(since, until).limit(limit)
        return list((await self._session.execute(stmt)).scalars())

    async def count_unanalyzed_frames(self, since: datetime, until: datetime) -> int:
        stmt = select(func.count()).select_from(self._unanalyzed(since, until).subquery())
        return int((await self._session.execute(stmt)).scalar_one())

    async def latest_marker(self) -> tuple[datetime, datetime, str | None] | None:
        """(frame_time, calculated_at, analysis) of the latest analyzed frame
        — enough to tell whether a cached computation over it is current,
        without loading its results."""
        latest = (
            await self._session.execute(select(func.max(SnifferResult.frame_time)))
        ).scalar_one_or_none()
        if latest is None:
            return None
        row = (
            await self._session.execute(
                select(SnifferResult.calculated_at, SnifferResult.analysis)
                .where(SnifferResult.frame_time == latest)
                .limit(1)
            )
        ).one()
        return latest, row[0], row[1]

    async def get_metrics_for_frame(
        self, frame_time: datetime, metrics: list[str]
    ) -> tuple[str | None, dict[str, dict[str, Decimal | None]]] | None:
        """Only `metrics` of one analyzed frame, as (analysis, {symbol:
        {metric: value}}) — or None if the frame was never analyzed."""
        stmt = (
            select(
                Instrument.symbol, SnifferResult.metric, SnifferResult.value, SnifferResult.analysis
            )
            .join(Instrument, Instrument.id == SnifferResult.instrument_id)
            .where(SnifferResult.frame_time == frame_time, SnifferResult.metric.in_(metrics))
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return None
        by_symbol: dict[str, dict[str, Decimal | None]] = {}
        for symbol, metric, value, _ in rows:
            by_symbol.setdefault(symbol, {})[metric] = value
        return rows[0][3], by_symbol

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
        analysis: str | None = rows[0][0].analysis
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
            analysis=analysis,
        )
