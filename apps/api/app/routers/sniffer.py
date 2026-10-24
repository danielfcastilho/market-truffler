import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, DbSessionDep
from app.domain.sniffer import SnifferFrameResult
from app.domain.temporal import DECISION_DELAY
from app.repositories.sniffer_repository import SnifferRepository
from app.schemas.rankings import LiveRankings
from app.schemas.sniffer import SnifferFrameResponse, SnifferInstrument, SnifferStatus
from app.services.collection_continuity import FINALIZE_ALLOWANCE
from app.services.live_rankings import live_rankings

router = APIRouter(tags=["sniffer"])
logger = logging.getLogger(__name__)


def _to_response(result: SnifferFrameResult, symbol: str | None = None) -> SnifferFrameResponse:
    """`symbol` narrows `instruments` to that one coin (audit F-08); the
    frame-level counts stay the whole frame's."""
    return SnifferFrameResponse(
        frame_time=result.frame_time,
        analyzed_at=result.analyzed_at,
        instruments_analyzed=len(result.instruments),
        instruments=[
            SnifferInstrument(
                instrument_id=i.instrument_id,
                symbol=i.symbol,
                return_5m=i.features.get("return_5m"),
                return_15m=i.features.get("return_15m"),
                return_1h=i.features.get("return_1h"),
                return_4h=i.features.get("return_4h"),
                return_24h=i.features.get("return_24h"),
                rsi_14_5m=i.features.get("rsi_14_5m"),
                rsi_14_15m=i.features.get("rsi_14_15m"),
                rsi_14_1h=i.features.get("rsi_14_1h"),
                rsi_14_4h=i.features.get("rsi_14_4h"),
                oi_change_5m=i.features.get("oi_change_5m"),
                oi_change_15m=i.features.get("oi_change_15m"),
                oi_change_1h=i.features.get("oi_change_1h"),
                oi_change_4h=i.features.get("oi_change_4h"),
                oi_change_24h=i.features.get("oi_change_24h"),
                volatility_15m=i.features.get("volatility_15m"),
                volatility_1h=i.features.get("volatility_1h"),
                volatility_4h=i.features.get("volatility_4h"),
                volatility_24h=i.features.get("volatility_24h"),
                relative_volume_15m=i.features.get("relative_volume_15m"),
                relative_volume_1h=i.features.get("relative_volume_1h"),
                relative_volume_4h=i.features.get("relative_volume_4h"),
                funding_rate_current=i.features.get("funding_rate_current"),
                funding_rate_24h_avg=i.features.get("funding_rate_24h_avg"),
            )
            for i in sorted(result.instruments, key=lambda i: i.symbol)
            if symbol is None or i.symbol == symbol
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
    repo = SnifferRepository(db)
    now = datetime.now(UTC)
    # A frame is normally analyzed by its decision time plus the analysis
    # itself; only past that allowance is a missing result a lost analysis.
    unanalyzed = await repo.count_unanalyzed_frames(
        now - timedelta(hours=24), now - DECISION_DELAY - FINALIZE_ALLOWANCE
    )
    result = await repo.get_latest()
    if result is None:
        return SnifferStatus(
            status=None, last_scan=None, coins_analyzed=None, frames_unanalyzed_24h=unanalyzed
        )
    return SnifferStatus(
        status="ok",
        last_scan=result.analyzed_at,
        coins_analyzed=len(result.instruments),
        frames_unanalyzed_24h=unanalyzed,
        last_analysis=result.analysis,
    )


@router.get("/api/sniffer/latest", response_model=SnifferFrameResponse)
async def latest_sniffer_result(
    db: DbSessionDep, current_user: CurrentUserDep, symbol: str | None = None
) -> SnifferFrameResponse:
    """Sniffer's most recently analyzed frame — rolling 5-minute and 60-minute returns.

    Ordered by symbol, a neutral deterministic ordering: this is a factual
    measurement, not a ranking, and this endpoint never sorts by return_5m
    or implies any instrument is more "interesting" than another.
    """
    result = await SnifferRepository(db).get_latest()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No Sniffer analysis yet")
    return _to_response(result, symbol.upper() if symbol else None)


@router.get("/api/sniffer/rankings", response_model=LiveRankings)
async def sniffer_rankings(
    db: DbSessionDep, current_user: CurrentUserDep, symbol: str | None = None
) -> LiveRankings:
    """Experimental Long and Short rankings of the latest analyzed frame, in
    one snapshot (both directions always rank the same frame). Read-only,
    computed from Sniffer features with the configured ranking candidates —
    not validated, not trading signals. `symbol` keeps only that coin's row
    and exclusion (ranked over the whole frame first)."""
    settings = get_settings()
    result = await live_rankings(
        db,
        candidates_dir=Path(settings.research_ranking_dir) / "candidates",
        long_candidate=settings.live_ranking_long_candidate,
        short_candidate=settings.live_ranking_short_candidate,
        now=datetime.now(UTC),
        symbol=symbol.upper() if symbol else None,
    )
    return LiveRankings.model_validate(result)


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
