from typing import Any

import pytest

from app.integrations.bybit.client import BybitApiError
from app.services.market_universe import MarketUniverseService, MarketUniverseUnavailable


def _instrument(
    symbol: str,
    contract_type: str = "LinearPerpetual",
    settle_coin: str = "USDT",
    status: str = "Trading",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "baseCoin": symbol.removesuffix("USDT"),
        "quoteCoin": "USDT",
        "settleCoin": settle_coin,
        "contractType": contract_type,
        "status": status,
    }


class FakeBybitClient:
    """Fakes the Bybit transport boundary so the service can be tested without HTTP."""

    def __init__(
        self, pages: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self._pages = pages or []
        self._error = error
        self.requested_cursors: list[str | None] = []

    async def get_instruments_info(
        self, category: str, cursor: str | None = None, limit: int = 1000
    ) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        self.requested_cursors.append(cursor)
        return self._pages[len(self.requested_cursors) - 1]


async def test_discover_universe_filters_to_eligible_instruments():
    client = FakeBybitClient(
        pages=[
            {
                "category": "linear",
                "list": [
                    _instrument("BTCUSDT"),
                    _instrument("ETHUSDPERP", settle_coin="USD"),  # wrong settle coin
                    _instrument("BTCUSD_25DEC26", contract_type="LinearFutures"),  # not perpetual
                    _instrument("SOLUSDT", status="PreLaunch"),  # not yet tradable
                    _instrument("XRPUSDT"),
                ],
                "nextPageCursor": "",
            }
        ]
    )
    service = MarketUniverseService(client)

    universe = await service.discover_universe()

    assert {i.symbol for i in universe} == {"BTCUSDT", "XRPUSDT"}


async def test_discover_universe_paginates_across_pages():
    client = FakeBybitClient(
        pages=[
            {"category": "linear", "list": [_instrument("BTCUSDT")], "nextPageCursor": "page2"},
            {"category": "linear", "list": [_instrument("ETHUSDT")], "nextPageCursor": ""},
        ]
    )
    service = MarketUniverseService(client)

    universe = await service.discover_universe()

    assert {i.symbol for i in universe} == {"BTCUSDT", "ETHUSDT"}
    assert client.requested_cursors == [None, "page2"]


async def test_discover_universe_raises_when_bybit_unavailable():
    client = FakeBybitClient(error=BybitApiError("boom"))
    service = MarketUniverseService(client)

    with pytest.raises(MarketUniverseUnavailable):
        await service.discover_universe()
