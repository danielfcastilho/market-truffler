import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, DbSessionDep, MarketCollectorDep, MarketUniverseServiceDep
from app.repositories.instrument_repository import InstrumentRepository
from app.schemas.market import MarketStatus
from app.services.historical_coverage import compute_historical_coverage
from app.services.market_universe import MarketUniverseUnavailable

router = APIRouter(tags=["market"])
logger = logging.getLogger(__name__)


@router.get("/api/market/status", response_model=MarketStatus)
async def market_status(
    universe_service: MarketUniverseServiceDep,
    collector: MarketCollectorDep,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> MarketStatus:
    """Truthful Bybit REST connectivity, market universe size, live MARKET
    collector state, and historical reconciliation progress, for Vitals.

    "bybit_connectivity"/"symbols_tracked" come from a fresh REST discovery
    call — if Bybit can't be reached, this reports "down" with no symbol
    count rather than raising.

    "market_data"/"last_market_update"/"data_freshness_seconds" are a
    read-only snapshot of the live MARKET collector, and
    "historical_coverage" a read-only snapshot of the background history
    reconciler's persisted progress. Both run continuously from application
    startup; this endpoint only observes them — it never starts, stops, or
    otherwise drives bootstrap, recovery, or WebSocket connections.
    """
    bybit_connectivity: Literal["ok", "down"]
    symbols_tracked: int | None
    try:
        universe = await universe_service.discover_universe()
        bybit_connectivity, symbols_tracked = "ok", len(universe)
    except MarketUniverseUnavailable:
        bybit_connectivity, symbols_tracked = "down", None

    collector_status = collector.status
    market_data: Literal["ok", "down"] = (
        "ok" if collector_status.connections_active > 0 else "down"
    )
    last_market_update = collector_status.last_candle_at
    data_freshness_seconds = (
        (datetime.now(UTC) - last_market_update).total_seconds() if last_market_update else None
    )

    active_instruments = await InstrumentRepository(db).list_active()
    historical_coverage = compute_historical_coverage(
        active_instruments, datetime.now(UTC), get_settings().market_history_retention_days
    )

    return MarketStatus(
        bybit_connectivity=bybit_connectivity,
        symbols_tracked=symbols_tracked,
        market_data=market_data,
        last_market_update=last_market_update,
        data_freshness_seconds=data_freshness_seconds,
        historical_coverage=historical_coverage,
    )
