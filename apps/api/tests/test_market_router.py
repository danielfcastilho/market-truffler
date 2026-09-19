from datetime import UTC, datetime, timedelta

from app.core.deps import get_market_collector, get_market_universe_service
from app.domain.market import Instrument
from app.main import app
from app.models.instrument import Instrument as InstrumentRow
from app.services.market_collector import MarketCollectorStatus
from app.services.market_universe import MarketUniverseUnavailable

RETENTION_DAYS = 365


class _FakeService:
    def __init__(self, universe: list[Instrument] | None = None, unavailable: bool = False) -> None:
        self._universe = universe or []
        self._unavailable = unavailable

    async def discover_universe(self) -> list[Instrument]:
        if self._unavailable:
            raise MarketUniverseUnavailable("bybit is down")
        return self._universe


class _FakeCollector:
    def __init__(self, status: MarketCollectorStatus) -> None:
        self.status = status


_IDLE_COLLECTOR_STATUS = MarketCollectorStatus()


async def _login(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


def _override(service=None, collector_status: MarketCollectorStatus = _IDLE_COLLECTOR_STATUS):
    app.dependency_overrides[get_market_universe_service] = lambda: service or _FakeService()
    app.dependency_overrides[get_market_collector] = lambda: _FakeCollector(collector_status)


def _clear_overrides():
    del app.dependency_overrides[get_market_universe_service]
    del app.dependency_overrides[get_market_collector]


async def test_market_status_requires_authentication(client):
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()
    assert response.status_code == 401


async def test_market_status_reports_ok_with_symbol_count(client, test_user):
    await _login(client, test_user)
    _override(
        service=_FakeService(
            universe=[
                Instrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT"),
                Instrument(symbol="ETHUSDT", base_coin="ETH", quote_coin="USDT"),
            ]
        )
    )
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["bybit_connectivity"] == "ok"
    assert body["symbols_tracked"] == 2


async def test_market_status_reports_down_when_bybit_unavailable(client, test_user):
    await _login(client, test_user)
    _override(service=_FakeService(unavailable=True))
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["bybit_connectivity"] == "down"
    assert body["symbols_tracked"] is None


async def test_market_status_reports_market_data_down_when_collector_has_no_active_connections(
    client, test_user
):
    await _login(client, test_user)
    _override(collector_status=MarketCollectorStatus(running=True, connections_active=0))
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["market_data"] == "down"
    assert body["last_market_update"] is None
    assert body["data_freshness_seconds"] is None


async def test_market_status_reports_market_data_ok_with_freshness_when_collector_is_live(
    client, test_user
):
    await _login(client, test_user)
    last_candle_at = datetime.now(UTC) - timedelta(seconds=12)
    _override(
        collector_status=MarketCollectorStatus(
            running=True,
            connections_active=3,
            candles_received=42,
            last_candle_at=last_candle_at,
        )
    )
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["market_data"] == "ok"
    assert body["last_market_update"] is not None
    assert body["data_freshness_seconds"] >= 12


async def test_market_status_reports_none_historical_coverage_with_no_active_instruments(
    client, test_user
):
    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.json()["historical_coverage"] is None


async def test_market_status_reports_real_historical_coverage_from_persisted_watermarks(
    client, test_user, db_session
):
    now = datetime.now(UTC)
    target_start = now - timedelta(days=RETENTION_DAYS)
    db_session.add(
        InstrumentRow(
            exchange="bybit",
            symbol="BTCUSDT",
            base_coin="BTC",
            quote_coin="USDT",
            is_active=True,
            first_seen_at=target_start,
            last_seen_at=now,
            history_target_start=target_start,
            history_synced_from=target_start,
            history_synced_through=now,
        )
    )
    await db_session.commit()

    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    # Not exactly 1.0: the router computes coverage against its own,
    # slightly-later `now()` than when this test seeded `history_synced_through`.
    assert response.json()["historical_coverage"] > 0.999


async def test_market_status_never_writes_to_the_database(client, test_user, db_session):
    """Vitals must stay a pure observer — reading /api/market/status must
    not create, mutate, or otherwise trigger any MARKET persistence work."""
    from sqlalchemy import select

    await _login(client, test_user)
    _override()
    try:
        await client.get("/api/market/status")
    finally:
        _clear_overrides()

    rows = (await db_session.execute(select(InstrumentRow))).scalars().all()
    assert rows == []
