import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from app.core.deps import CurrentUserDep, DbSessionDep
from app.domain.sniffer import SnifferFrameResult
from app.repositories.sniffer_repository import SnifferRepository
from app.schemas.sniffer import SnifferFrameResponse, SnifferInstrument, SnifferStatus

router = APIRouter(tags=["sniffer"])
logger = logging.getLogger(__name__)


def _to_response(result: SnifferFrameResult) -> SnifferFrameResponse:
    return SnifferFrameResponse(
        frame_time=result.frame_time,
        analyzed_at=result.analyzed_at,
        instruments_analyzed=len(result.instruments),
        instruments=[
            SnifferInstrument(
                instrument_id=i.instrument_id,
                symbol=i.symbol,
                return_5m=i.features.get("return_5m"),
                return_1h=i.features.get("return_1h"),
                rsi_14_5m=i.features.get("rsi_14_5m"),
                rsi_14_15m=i.features.get("rsi_14_15m"),
                rsi_14_1h=i.features.get("rsi_14_1h"),
                rsi_14_4h=i.features.get("rsi_14_4h"),
            )
            for i in sorted(result.instruments, key=lambda i: i.symbol)
        ],
    )


@router.get("/api/sniffer/status", response_model=SnifferStatus)
async def sniffer_status(db: DbSessionDep, current_user: CurrentUserDep) -> SnifferStatus:
    """Truthful Sniffer status for Vitals.

    Read-only snapshot of Sniffer's own persisted results — this never
    triggers analysis. Sniffer has no background loop of its own; it only
    ever runs reactively when MARKET finalizes a frame (see
    `app.services.sniffer`).
    """
    result = await SnifferRepository(db).get_latest()
    if result is None:
        return SnifferStatus(status=None, last_scan=None, coins_analyzed=None)
    return SnifferStatus(
        status="ok", last_scan=result.analyzed_at, coins_analyzed=len(result.instruments)
    )


@router.get("/api/sniffer/latest", response_model=SnifferFrameResponse)
async def latest_sniffer_result(
    db: DbSessionDep, current_user: CurrentUserDep
) -> SnifferFrameResponse:
    """Sniffer's most recently analyzed frame — rolling 5-minute and 60-minute returns.

    Ordered by symbol, a neutral deterministic ordering: this is a factual
    measurement, not a ranking, and this endpoint never sorts by return_5m
    or implies any instrument is more "interesting" than another.
    """
    result = await SnifferRepository(db).get_latest()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No Sniffer analysis yet")
    return _to_response(result)


@router.get("/api/sniffer/frames/{frame_time}", response_model=SnifferFrameResponse)
async def sniffer_result_for_frame(
    frame_time: datetime, db: DbSessionDep, current_user: CurrentUserDep
) -> SnifferFrameResponse:
    """Sniffer's analysis of one specific frame_time, if it exists."""
    result = await SnifferRepository(db).get_for_frame(frame_time)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No Sniffer analysis for that frame_time"
        )
    return _to_response(result)
