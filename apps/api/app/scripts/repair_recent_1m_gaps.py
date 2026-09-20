"""One-off maintenance: close 1m candle gaps within the recent lookback
window RSI actually depends on (the last ~68 hours, comfortably covering
rsi_14_4h's 60-hour requirement).

These gaps predate the fix to `HistoryReconciler._catchup_step` (it used
to advance `history_synced_through` unconditionally after a single REST
attempt, even when that attempt came back short — see the reconciler's
docstring/comments). Its watermark has already sailed past this whole
window, so ordinary forward catch-up will never revisit it on its own;
this script reuses the reconciler's own (now-fixed) gap-detection/REST-
fill/bounded-retry methods directly, scoped to the affected historical
range, then re-derives the higher timeframes over whatever was touched.

Does not touch any candle outside this window, and does not fabricate or
interpolate anything: a minute still missing after the retry is accepted
exactly like the reconciler accepts it — a genuine, honest gap.

Usage (inside the api container):

    python -m app.scripts.repair_recent_1m_gaps
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.integrations.bybit.client import BybitClient
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.services.candle_aggregation import derive_higher_timeframes
from app.services.candle_ingestion import CandleIngestionService
from app.services.history_reconciler import HistoryReconciler
from app.services.market_universe import MarketUniverseService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_LOOKBACK = timedelta(hours=68)
_CHUNK = timedelta(hours=1)
_SECOND_RETRY_DELAY = 0.5


async def _repair_instrument(
    reconciler: HistoryReconciler,
    session_factory,
    instrument_id: int,
    window_start: datetime,
    now: datetime,
) -> int:
    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        if instrument is None:
            return 0
        symbol = instrument.symbol

    touched_ranges: list[tuple[datetime, datetime]] = []
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        candle_ingestion = CandleIngestionService(candle_repo)
        chunk_start = window_start
        while chunk_start < now:
            chunk_end = min(chunk_start + _CHUNK, now)
            # Reuses the reconciler's own fixed methods directly rather
            # than reimplementing gap-detection/backoff a second time.
            if not await reconciler._chunk_complete(  # noqa: SLF001
                candle_repo, instrument_id, chunk_start, chunk_end
            ):
                await reconciler._fetch_and_ingest_page(  # noqa: SLF001
                    instrument_id, symbol, candle_ingestion, chunk_start, chunk_end
                )
                if not await reconciler._chunk_complete(  # noqa: SLF001
                    candle_repo, instrument_id, chunk_start, chunk_end
                ):
                    await asyncio.sleep(_SECOND_RETRY_DELAY)
                    await reconciler._fetch_and_ingest_page(  # noqa: SLF001
                        instrument_id, symbol, candle_ingestion, chunk_start, chunk_end
                    )
                touched_ranges.append((chunk_start, chunk_end))
            chunk_start = chunk_end

        for start, end in touched_ranges:
            await derive_higher_timeframes(candle_repo, instrument_id, start, end)

    return len(touched_ranges)


async def repair_all() -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    bybit_client = BybitClient(
        base_url=settings.bybit_base_url, timeout_seconds=settings.bybit_timeout_seconds
    )
    market_universe = MarketUniverseService(bybit_client)
    reconciler = HistoryReconciler(
        session_factory,
        bybit_client,
        market_universe,
        retention_days=settings.market_history_retention_days,
        page_size=settings.market_history_page_size,
    )

    async with session_factory() as session:
        instrument_ids = list(
            (
                await session.execute(select(Instrument.id).where(Instrument.is_active))
            ).scalars()
        )

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    window_start = now - _LOOKBACK

    logger.info(
        "repair_starting",
        extra={"instruments": len(instrument_ids), "window_start": window_start.isoformat()},
    )
    total_gap_chunks = 0
    for i, instrument_id in enumerate(instrument_ids, start=1):
        try:
            gap_chunks = await _repair_instrument(
                reconciler, session_factory, instrument_id, window_start, now
            )
            total_gap_chunks += gap_chunks
        except Exception:  # noqa: BLE001 - one bad instrument must not kill the run
            logger.exception("instrument_repair_failed", extra={"instrument_id": instrument_id})
        if i % 50 == 0:
            logger.info(
                "repair_progress",
                extra={
                    "done": i,
                    "total": len(instrument_ids),
                    "gap_chunks_so_far": total_gap_chunks,
                },
            )
    logger.info("repair_complete", extra={"total_gap_chunks_touched": total_gap_chunks})


def main() -> None:
    asyncio.run(repair_all())


if __name__ == "__main__":
    main()
