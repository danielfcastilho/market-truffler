"""The single canonical base-timeframe (5m) ingestion boundary.

Both the live WebSocket collector and the REST bootstrap/recovery walker
funnel their closed 5m candles through here, so persistence, idempotency,
and aggregate derivation are implemented exactly once regardless of
transport — overlap between them (invariant per M3 section 8) is always
harmless.
"""

from app.domain.market import ClosedCandle
from app.domain.timeframes import BASE_DURATION, BASE_TIMEFRAME
from app.repositories.candle_repository import CandleRepository
from app.services.candle_aggregation import derive_higher_timeframes


class CandleIngestionService:
    def __init__(self, candle_repo: CandleRepository) -> None:
        self._candle_repo = candle_repo

    async def ingest_base_candles(self, instrument_id: int, candles: list[ClosedCandle]) -> None:
        """Upsert a batch of closed base-timeframe candles for one
        instrument, then (re)derive any 15m/1h/4h/24h windows the batch now
        completes or corrects.

        A batch may be a single live candle or an entire REST page — either
        way this is one upsert statement plus one bounded re-check of the
        touched range, never a per-row transaction. Callers are responsible
        for only ever passing closed candles (see `MarketCollector`'s
        `confirm` check and `HistoryReconciler`'s closed-row filter).
        """
        if not candles:
            return

        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": BASE_TIMEFRAME,
                "open_time": c.open_time,
                "close_time": c.close_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "turnover": c.turnover,
            }
            for c in candles
        ]
        await self._candle_repo.upsert_many(rows)

        touched_start = min(c.open_time for c in candles)
        touched_end = max(c.open_time for c in candles) + BASE_DURATION
        await derive_higher_timeframes(self._candle_repo, instrument_id, touched_start, touched_end)
