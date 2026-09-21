from datetime import UTC, datetime, timedelta

import pytest

from app.core.deps import get_history_reconciler, get_market_collector
from app.main import app
from app.models.frame import MarketFrame, MarketFrameMember
from app.models.instrument import Instrument as InstrumentRow
from app.repositories.frame_repository import FrameRepository
from app.services.history_reconciler import HistoryReconcilerStatus
from app.services.market_collector import MarketCollectorStatus

RETENTION_DAYS = 365


class _FakeReconciler:
    def __init__(self, status: HistoryReconcilerStatus) -> None:
        self.status = status


class _FakeCollector:
    def __init__(self, status: MarketCollectorStatus) -> None:
        self.status = status


_IDLE_COLLECTOR_STATUS = MarketCollectorStatus()
# Mirrors the real HistoryReconciler's state immediately after its startup
# universe refresh succeeds — the common case for these tests, none of
# which are exercising bybit_connectivity itself.
_OK_RECONCILER_STATUS = HistoryReconcilerStatus(
    last_universe_refresh_at=datetime.now(UTC), last_universe_refresh_ok=True
)


async def _seed_active_instruments(db_session, symbols: list[str]) -> None:
    now = datetime.now(UTC)
    for symbol in symbols:
        db_session.add(
            InstrumentRow(
                exchange="bybit",
                symbol=symbol,
                base_coin=symbol.removesuffix("USDT"),
                quote_coin="USDT",
                is_active=True,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    await db_session.commit()


async def _login(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


def _override(
    reconciler_status: HistoryReconcilerStatus = _OK_RECONCILER_STATUS,
    collector_status: MarketCollectorStatus = _IDLE_COLLECTOR_STATUS,
):
    app.dependency_overrides[get_history_reconciler] = lambda: _FakeReconciler(reconciler_status)
    app.dependency_overrides[get_market_collector] = lambda: _FakeCollector(collector_status)


def _clear_overrides():
    del app.dependency_overrides[get_history_reconciler]
    del app.dependency_overrides[get_market_collector]


async def test_market_status_requires_authentication(client):
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()
    assert response.status_code == 401


async def test_market_status_never_calls_bybit_directly(client, test_user, db_session, monkeypatch):
    """The whole point of this fix: opening Vitals must never itself issue a
    live Bybit REST call. `bybit_connectivity`/`symbols_tracked` must come
    from already-maintained state (the reconciler's own status, and a
    persisted instrument count) — never a fresh `discover_universe()`."""
    from app.services.market_universe import MarketUniverseService

    async def _fail_if_called(self, *args, **kwargs):
        raise AssertionError("market_status must never call Bybit directly")

    monkeypatch.setattr(MarketUniverseService, "discover_universe", _fail_if_called)

    await _seed_active_instruments(db_session, ["BTCUSDT", "ETHUSDT"])
    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["bybit_connectivity"] == "ok"
    assert body["symbols_tracked"] == 2


async def test_market_status_never_hydrates_full_frame_member_candle_data(
    client, test_user, monkeypatch
):
    """The other half of the fix: market_status must use
    `get_latest_finalized_summary` (frame-level metadata only), never
    `get_latest_finalized`/`_fetch_members`'s per-member OHLCV join — that
    join alone measured ~16s locally and was the dominant Vitals cost."""

    async def _fail_if_called(self, *args, **kwargs):
        raise AssertionError("market_status must never hydrate full frame members")

    monkeypatch.setattr(FrameRepository, "_fetch_members", _fail_if_called)

    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200


async def test_market_status_reports_ok_with_symbol_count(client, test_user, db_session):
    await _seed_active_instruments(db_session, ["BTCUSDT", "ETHUSDT"])
    await _login(client, test_user)
    _override(reconciler_status=_OK_RECONCILER_STATUS)
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["bybit_connectivity"] == "ok"
    assert body["symbols_tracked"] == 2


async def test_market_status_reports_down_when_last_universe_refresh_failed(client, test_user):
    await _login(client, test_user)
    _override(
        reconciler_status=HistoryReconcilerStatus(
            last_universe_refresh_at=datetime.now(UTC), last_universe_refresh_ok=False
        )
    )
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["bybit_connectivity"] == "down"


async def test_market_status_treats_no_refresh_yet_the_same_as_down(client, test_user):
    """`last_universe_refresh_ok=None` (hasn't run yet) can't claim
    reachability either way — Vitals must not report "ok" for it."""
    await _login(client, test_user)
    _override(reconciler_status=HistoryReconcilerStatus())
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    assert response.json()["bybit_connectivity"] == "down"


async def test_market_status_symbols_tracked_stays_a_real_count_even_when_bybit_is_down(
    client, test_user, db_session
):
    """symbols_tracked is a persisted fact, decoupled from the live Bybit
    probe outcome — it must not collapse to N/A just because the most
    recent background refresh happened to fail."""
    await _seed_active_instruments(db_session, ["BTCUSDT"])
    await _login(client, test_user)
    _override(
        reconciler_status=HistoryReconcilerStatus(
            last_universe_refresh_at=datetime.now(UTC), last_universe_refresh_ok=False
        )
    )
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["bybit_connectivity"] == "down"
    assert body["symbols_tracked"] == 1


async def test_market_status_symbols_tracked_is_zero_not_na_with_no_active_instruments(
    client, test_user
):
    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    # A real, empty repository read — 0 is truthful, never N/A.
    assert response.json()["symbols_tracked"] == 0


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


async def test_market_status_reports_na_frame_fields_when_no_frame_exists_yet(client, test_user):
    await _login(client, test_user)
    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["latest_market_frame"] is None
    assert body["frame_completeness"] is None


async def test_market_status_reports_the_real_latest_finalized_frame(client, test_user, db_session):
    # Login first: FrameRepository's writes expire this session's identity
    # map (same footgun CandleRepository.upsert_many has — see its
    # docstring), which would otherwise leave the already-loaded `test_user`
    # ORM object stale and trigger a lazy-refresh outside async context.
    await _login(client, test_user)

    frame_time = datetime(2026, 1, 1, 14, 37, tzinfo=UTC)
    repo = FrameRepository(db_session)
    await repo.create_building(frame_time, expected_instrument_ids=[1, 2], now=datetime.now(UTC))
    await repo.finalize(
        frame_time,
        [
            {
                "frame_time": frame_time,
                "instrument_id": 1,
                "open_time_1m": frame_time,
                "open_time_5m": frame_time,
                "open_time_15m": frame_time,
                "open_time_1h": frame_time,
            }
        ],
        now=datetime.now(UTC),
    )

    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["latest_market_frame"] == "2026-01-01T14:37:00Z"
    assert body["frame_completeness"] == 0.5  # 1 of 2 expected — PARTIAL, truthfully reported


async def test_market_status_ignores_a_still_building_frame(client, test_user, db_session):
    await _login(client, test_user)  # login first — see comment above

    frame_time = datetime(2026, 1, 1, 14, 37, tzinfo=UTC)
    await FrameRepository(db_session).create_building(
        frame_time, expected_instrument_ids=[1, 2, 3, 4, 5], now=datetime.now(UTC)
    )

    _override()
    try:
        response = await client.get("/api/market/status")
    finally:
        _clear_overrides()

    body = response.json()
    assert body["latest_market_frame"] is None  # not yet finalized — must not surface as "latest"
    assert body["frame_completeness"] is None


async def test_market_status_never_creates_a_frame(client, test_user, db_session):
    from sqlalchemy import select

    await _login(client, test_user)
    _override()
    try:
        await client.get("/api/market/status")
    finally:
        _clear_overrides()

    frames = (await db_session.execute(select(MarketFrame))).scalars().all()
    members = (await db_session.execute(select(MarketFrameMember))).scalars().all()
    assert frames == []
    assert members == []


@pytest.mark.parametrize("retention_days,expected", [(30, 0.5), (60, 0.25)])
async def test_status_uses_runtime_retention_and_only_active_instruments(
    client, test_user, db_session, monkeypatch, retention_days, expected
):
    from app.core.config import get_settings

    monkeypatch.setenv("MARKET_HISTORY_RETENTION_DAYS", str(retention_days))
    get_settings.cache_clear()
    now = datetime.now(UTC)
    for symbol, active in [("ACTIVEUSDT", True), ("INACTIVEUSDT", False)]:
        db_session.add(
            InstrumentRow(
                exchange="bybit",
                symbol=symbol,
                base_coin=symbol,
                quote_coin="USDT",
                is_active=active,
                first_seen_at=now,
                last_seen_at=now,
                history_target_start=now - timedelta(days=365),
                history_synced_from=now - timedelta(days=15) if active else now,
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
        get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json()["historical_coverage"] == pytest.approx(expected, abs=0.0001)


def test_default_history_policy_is_30_days():
    from app.core.config import Settings

    settings = Settings(_env_file=None, session_secret="x" * 32, market_history_retention_days=30)
    assert Settings.model_fields["market_history_retention_days"].default == 30
    assert settings.market_history_retention_days == 30
