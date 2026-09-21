import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, DbSessionDep, HistoryReconcilerDep, MarketCollectorDep
from app.domain.frame import MarketFrame as MarketFrameDomain
from app.repositories.frame_repository import FrameRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.schemas.frame import FrameCandleContext, FrameMember, MarketFrameResponse
from app.schemas.market import MarketStatus
from app.services.historical_coverage import compute_historical_coverage

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
    reconciler: HistoryReconcilerDep,
    collector: MarketCollectorDep,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> MarketStatus:
    """Truthful Bybit REST connectivity, market universe size, live MARKET
    collector state, and historical reconciliation progress, for Vitals.

    Vitals is an observer: it must never trigger expensive operational work
    merely to render a status page. This endpoint had two real, independent
    latency bugs, both fixed here — a measured ~1.2-2s one and a much larger
    measured ~16-28s one; the second was by far the dominant cost, so fixing
    only the first would not have made Vitals materially faster:

    - "bybit_connectivity" reads `HistoryReconciler`'s own most recent
      universe-refresh outcome (`reconciler.status`) — the reconciler
      already calls Bybit's REST `instruments-info` endpoint periodically
      in the background (and once synchronously on startup, before it ever
      starts serving requests), so this is a genuinely live signal with no
      additional network I/O. This endpoint used to call
      `MarketUniverseService.discover_universe()` directly on every
      request instead; that single call alone measured ~1.2-2s against
      real Bybit (a large, apparently uncached `instruments-info`
      response).
    - "symbols_tracked" reads the persisted active-instrument count
      (`active_instruments` below, already needed for
      "historical_coverage") instead of a fresh discovery call — the same
      background reconciliation keeps it current, and it stays a truthful
      "how many symbols do we track" answer even in the rare window where
      the live Bybit probe itself is currently failing.
    - "latest_market_frame"/"frame_completeness" now read
      `FrameRepository.get_latest_finalized_summary` (frame-level metadata
      only) instead of `get_latest_finalized`, which unconditionally joins
      every member's full OHLCV context across all five timeframes — a
      join that alone measured ~16s locally (unbounded per-timeframe
      candle subqueries) and was actually the single largest contributor
      to Vitals' load time, well past the REST call above. This endpoint
      never needed that per-member candle data in the first place.

    "market_data"/"last_market_update"/"data_freshness_seconds" are a
    read-only snapshot of the live MARKET collector, and
    "historical_coverage" a read-only snapshot of the background history
    reconciler's persisted progress. All of this runs continuously from
    application startup; this endpoint only observes it — it never starts,
    stops, or otherwise drives bootstrap, recovery, WebSocket connections,
    universe discovery, or frame construction.
    """
    reconciler_status = reconciler.status
    bybit_connectivity: Literal["ok", "down"] = (
        "ok" if reconciler_status.last_universe_refresh_ok else "down"
    )

    collector_status = collector.status
    market_data: Literal["ok", "down"] = (
        "ok" if collector_status.connections_active > 0 else "down"
    )
    last_market_update = collector_status.last_candle_at
    data_freshness_seconds = (
        (datetime.now(UTC) - last_market_update).total_seconds() if last_market_update else None
    )

    active_instruments = await InstrumentRepository(db).list_active()
    # A real (possibly empty) repository read, not a live probe — 0 is a
    # truthful count here, never conflated with "unavailable"/N/A.
    symbols_tracked: int | None = len(active_instruments)
    historical_coverage = compute_historical_coverage(
        active_instruments, datetime.now(UTC), get_settings().market_history_retention_days
    )

    # Metadata only (frame_time/completeness) — never the fully-hydrated
    # per-member OHLCV join `get_latest_finalized` does, which this row
    # doesn't need and which was by far the most expensive part of this
    # endpoint (see `FrameRepository.get_latest_finalized_summary`).
    latest_frame = await FrameRepository(db).get_latest_finalized_summary()
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
