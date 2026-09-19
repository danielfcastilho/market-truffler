"""Market universe discovery: turns Bybit's raw instrument list into the
set of instruments Market Truffler cares about.

Sits between the Bybit transport boundary (`app.integrations.bybit`) and API
presentation (`app.routers.market`) — nothing above this layer should know
Bybit-specific field names, categories, or pagination cursors.
"""

import logging

from app.domain.market import Instrument
from app.integrations.bybit.client import BybitApiError, BybitClient
from app.integrations.bybit.schemas import InstrumentsInfoResult

logger = logging.getLogger(__name__)

# The market universe this milestone cares about: linear, USDT-settled,
# perpetual contracts that are currently tradable.
_CATEGORY = "linear"
_ELIGIBLE_CONTRACT_TYPE = "LinearPerpetual"
_ELIGIBLE_SETTLE_COIN = "USDT"
_ELIGIBLE_STATUS = "Trading"


class MarketUniverseUnavailable(Exception):
    """Raised when the market universe cannot be discovered right now."""


class MarketUniverseService:
    def __init__(self, client: BybitClient) -> None:
        self._client = client

    async def discover_universe(self) -> list[Instrument]:
        """Return every currently-eligible instrument, paginating as needed."""
        instruments: list[Instrument] = []
        cursor: str | None = None

        try:
            while True:
                raw_page = await self._client.get_instruments_info(_CATEGORY, cursor=cursor)
                page = InstrumentsInfoResult.model_validate(raw_page)
                instruments.extend(
                    Instrument(
                        symbol=item.symbol, base_coin=item.baseCoin, quote_coin=item.quoteCoin
                    )
                    for item in page.list
                    if item.contractType == _ELIGIBLE_CONTRACT_TYPE
                    and item.settleCoin == _ELIGIBLE_SETTLE_COIN
                    and item.status == _ELIGIBLE_STATUS
                )

                if not page.nextPageCursor:
                    break
                cursor = page.nextPageCursor
        except BybitApiError as exc:
            logger.warning("market_universe_discovery_failed", extra={"error": str(exc)})
            raise MarketUniverseUnavailable(str(exc)) from exc

        return instruments
