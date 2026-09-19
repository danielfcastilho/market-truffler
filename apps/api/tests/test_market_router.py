from app.core.deps import get_market_universe_service
from app.domain.market import Instrument
from app.main import app
from app.services.market_universe import MarketUniverseUnavailable


class _FakeService:
    def __init__(self, universe: list[Instrument] | None = None, unavailable: bool = False) -> None:
        self._universe = universe or []
        self._unavailable = unavailable

    async def discover_universe(self) -> list[Instrument]:
        if self._unavailable:
            raise MarketUniverseUnavailable("bybit is down")
        return self._universe


async def _login(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


async def test_market_status_requires_authentication(client):
    response = await client.get("/api/market/status")
    assert response.status_code == 401


async def test_market_status_reports_ok_with_symbol_count(client, test_user):
    await _login(client, test_user)
    app.dependency_overrides[get_market_universe_service] = lambda: _FakeService(
        universe=[
            Instrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT"),
            Instrument(symbol="ETHUSDT", base_coin="ETH", quote_coin="USDT"),
        ]
    )
    try:
        response = await client.get("/api/market/status")
    finally:
        del app.dependency_overrides[get_market_universe_service]

    assert response.status_code == 200
    assert response.json() == {"bybit_connectivity": "ok", "symbols_tracked": 2}


async def test_market_status_reports_down_when_bybit_unavailable(client, test_user):
    await _login(client, test_user)
    app.dependency_overrides[get_market_universe_service] = lambda: _FakeService(unavailable=True)
    try:
        response = await client.get("/api/market/status")
    finally:
        del app.dependency_overrides[get_market_universe_service]

    assert response.status_code == 200
    assert response.json() == {"bybit_connectivity": "down", "symbols_tracked": None}
