import logging

from fastapi import APIRouter

from app.core.deps import CurrentUserDep, MarketUniverseServiceDep
from app.schemas.market import MarketStatus
from app.services.market_universe import MarketUniverseUnavailable

router = APIRouter(tags=["market"])
logger = logging.getLogger(__name__)


@router.get("/api/market/status", response_model=MarketStatus)
async def market_status(
    service: MarketUniverseServiceDep, current_user: CurrentUserDep
) -> MarketStatus:
    """Truthful Bybit connectivity + market universe size, for Vitals.

    Bybit is an external dependency: if it can't be reached, this reports
    connectivity as "down" with no symbol count rather than raising, so a
    Bybit outage never takes this endpoint (or the app) down with it.
    """
    try:
        universe = await service.discover_universe()
    except MarketUniverseUnavailable:
        return MarketStatus(bybit_connectivity="down", symbols_tracked=None)

    return MarketStatus(bybit_connectivity="ok", symbols_tracked=len(universe))
