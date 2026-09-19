"""Turns MARKET's live WebSocket candles into real persistence.

`MarketCollector` knows nothing about the database — it just calls a sink
with each confirmed candle (see `app.services.market_collector`). This is
that sink for M3: it resolves symbol -> instrument_id (cached, periodically
refreshed so the hot path doesn't hit the database per candle) and funnels
each candle through the same `CandleIngestionService` the REST history
reconciler uses, so live and historical delivery share one persistence and
aggregation path.
"""

import logging
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.market import ClosedCandle
from app.repositories.candle_repository import CandleRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.services.candle_ingestion import CandleIngestionService

logger = logging.getLogger(__name__)


class PersistingCandleSink:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        mapping_refresh_seconds: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._mapping_refresh_seconds = mapping_refresh_seconds
        self._symbol_to_instrument_id: dict[str, int] = {}
        self._last_refresh_monotonic: float = 0.0

    async def _ensure_fresh_mapping(self) -> None:
        now = time.monotonic()
        if self._symbol_to_instrument_id and (
            now - self._last_refresh_monotonic
        ) < self._mapping_refresh_seconds:
            return

        async with self._session_factory() as session:
            active = await InstrumentRepository(session).list_active()

        self._symbol_to_instrument_id = {row.symbol: row.id for row in active}
        self._last_refresh_monotonic = now

    async def __call__(self, candle: ClosedCandle) -> None:
        await self._ensure_fresh_mapping()

        instrument_id = self._symbol_to_instrument_id.get(candle.symbol)
        if instrument_id is None:
            # Universe reconciliation hasn't (yet) registered this symbol —
            # normal for the brief window right after a new WS subscription
            # goes live, before the next mapping refresh picks it up.
            logger.debug("live_candle_unresolved_instrument", extra={"symbol": candle.symbol})
            return

        async with self._session_factory() as session:
            candle_repo = CandleRepository(session)
            await CandleIngestionService(candle_repo).ingest_1m_candles(instrument_id, [candle])
