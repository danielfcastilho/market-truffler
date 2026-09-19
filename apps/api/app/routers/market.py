import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, DbSessionDep, MarketCollectorDep, MarketUniverseServiceDep
from app.domain.frame import MarketFrame as MarketFrameDomain
from app.repositories.frame_repository import FrameRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.schemas.frame import FrameCandleContext, FrameMember, MarketFrameResponse
from app.schemas.market import MarketStatus
from app.services.historical_coverage import compute_historical_coverage
from app.services.market_universe import MarketUniverseUnavailable

router = APIRouter(tags=["market"])
logger = logging.getLogger(__name__)


def _to_frame_response(frame: MarketFrameDomain) -> MarketFrameResponse:
    return MarketFrameResponse(
        frame_time=frame.frame_time,
        expected_instruments=frame.expected_instruments,
        available_instruments=frame.available_instruments,
        completeness=frame.completeness,
        status=frame.status.value,
        created_at=frame.created_at,
        finalized_at=frame.finalized_at,
        members=[
            FrameMember(
                instrument_id=m.instrument_id,
                symbol=m.symbol,
                m1=FrameCandleContext(**m.m1.__dict__),
                m5=FrameCandleContext(**m.m5.__dict__),
                m15=FrameCandleContext(**m.m15.__dict__),
                h1=FrameCandleContext(**m.h1.__dict__),
            )
            for m in frame.members
        ],
    )


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
    read-only snapshot of the live MARKET collector, "historical_coverage"
    a read-only snapshot of the background history reconciler's persisted
    progress, and "latest_market_frame"/"frame_completeness" a read-only
    snapshot of the most recently finalized Market Frame. All three run
    continuously from application startup; this endpoint only observes
    them — it never starts, stops, or otherwise drives bootstrap, recovery,
    WebSocket connections, or frame construction.
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

    latest_frame = await FrameRepository(db).get_latest_finalized()
    latest_market_frame = latest_frame.frame_time if latest_frame else None
    frame_completeness = latest_frame.completeness if latest_frame else None

    return MarketStatus(
        bybit_connectivity=bybit_connectivity,
        symbols_tracked=symbols_tracked,
        market_data=market_data,
        last_market_update=last_market_update,
        data_freshness_seconds=data_freshness_seconds,
        historical_coverage=historical_coverage,
        latest_market_frame=latest_market_frame,
        frame_completeness=frame_completeness,
    )


@router.get("/api/market/frames/latest", response_model=MarketFrameResponse)
async def latest_market_frame_detail(
    db: DbSessionDep, current_user: CurrentUserDep
) -> MarketFrameResponse:
    """The most recently finalized Market Frame, with full joined OHLCV
    context per member — for inspection/debugging. Read-only: this never
    creates a frame (see `app.services.frame_synchronizer`)."""
    frame = await FrameRepository(db).get_latest_finalized()
    if frame is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No finalized Market Frame yet")
    return _to_frame_response(frame)


@router.get("/api/market/frames/{frame_time}", response_model=MarketFrameResponse)
async def market_frame_detail(
    frame_time: datetime, db: DbSessionDep, current_user: CurrentUserDep
) -> MarketFrameResponse:
    """A specific Market Frame by its exact frame_time, with full joined
    OHLCV context per member. Read-only."""
    frame = await FrameRepository(db).get_frame(frame_time)
    if frame is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No Market Frame at that frame_time")
    return _to_frame_response(frame)
