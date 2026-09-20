"""Maintenance: backfill 5m/15m/1h/4h aggregates for already-stored 1m
history that predates the aggregation logic ever touching that range —
e.g. right after a new derived timeframe (4h) is added, since
`derive_higher_timeframes` only ever runs reactively on newly-ingested
ranges (live ticks, bootstrap pages, recovery, corrections), never
retroactively on old ranges by itself.

This is a pure, deterministic recomputation from canonical 1m candles
already sitting in `market_candles` — it never calls Bybit and never
invents a value; it is exactly `app.services.candle_aggregation
.derive_higher_timeframes`, the same idempotent function every live/REST
ingestion path already calls, just run once over each instrument's full
existing 1m history instead of one small touched range. Safe to re-run.

Processed in bounded day-sized chunks, not one call over the whole
history: `CandleRepository.upsert_many` issues a single multi-row INSERT,
and Postgres caps a statement at 65535 bind parameters — 10 columns/row
means ~6500 rows/statement, and a single 5m derivation over more than
~22 days of history (288 buckets/day) can exceed that in one call. A
one-day window (max 288 5m buckets/day) stays comfortably under the limit
for every derived timeframe.

Usage (inside the api container):

    python -m app.scripts.backfill_derived_timeframes
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.services.candle_aggregation import derive_higher_timeframes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CHUNK = timedelta(days=1)


async def _backfill_instrument(session_factory, instrument_id: int) -> None:
    async with session_factory() as session:
        bounds = (
            await session.execute(
                select(func.min(Candle.open_time), func.max(Candle.open_time)).where(
                    Candle.instrument_id == instrument_id, Candle.timeframe == "1m"
                )
            )
        ).one()
        earliest, latest = bounds
    if earliest is None:
        return

    totals: dict[str, int] = {}
    chunk_start = earliest
    end = latest + timedelta(minutes=1)
    while chunk_start < end:
        chunk_end = min(chunk_start + _CHUNK, end)
        async with session_factory() as session:
            candle_repo = CandleRepository(session)
            derived = await derive_higher_timeframes(
                candle_repo, instrument_id, chunk_start, chunk_end
            )
        for timeframe, count in derived.items():
            totals[timeframe] = totals.get(timeframe, 0) + count
        chunk_start = chunk_end

    logger.info("instrument_backfilled", extra={"instrument_id": instrument_id, **totals})


async def backfill_all() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        instrument_ids = list((await session.execute(select(Instrument.id))).scalars())

    logger.info("backfill_starting", extra={"instruments": len(instrument_ids)})
    for i, instrument_id in enumerate(instrument_ids, start=1):
        try:
            await _backfill_instrument(session_factory, instrument_id)
        except Exception:  # noqa: BLE001 - one bad instrument must not kill the run
            logger.exception("instrument_backfill_failed", extra={"instrument_id": instrument_id})
        if i % 50 == 0:
            logger.info("backfill_progress", extra={"done": i, "total": len(instrument_ids)})
    logger.info("backfill_complete")


def main() -> None:
    asyncio.run(backfill_all())


if __name__ == "__main__":
    main()
