from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.frame import MarketFrame as MarketFrameRow
from app.models.instrument import Instrument
from app.models.sniffer import SnifferResult
from app.repositories.sniffer_repository import SnifferRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
ANALYZED_AT = datetime(2026, 1, 1, 14, 0, 6, tzinfo=UTC)


async def _login(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


async def _seed_frame(db_session, frame_time: datetime = FRAME_TIME) -> None:
    db_session.add(
        MarketFrameRow(
            frame_time=frame_time,
            expected_instrument_ids=[],
            expected_instruments=0,
            available_instruments=0,
            status="complete",
            created_at=frame_time,
            finalized_at=frame_time,
        )
    )
    await db_session.commit()


async def _seed_analyzed_frame(db_session) -> None:
    """Two instruments, one with a value and one genuinely unavailable —
    also seeded out of alphabetical order (ETH then BTC) to prove the
    router sorts by symbol rather than returning insertion/repository
    order or return_5m order."""
    await _seed_frame(db_session)
    now = datetime.now(UTC)
    eth = Instrument(
        exchange="bybit",
        symbol="ETHUSDT",
        base_coin="ETH",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    btc = Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add_all([eth, btc])
    await db_session.commit()
    await db_session.refresh(eth)
    await db_session.refresh(btc)

    repo = SnifferRepository(db_session)
    await repo.save_result(
        SnifferFrameResult(
            frame_time=FRAME_TIME,
            analyzed_at=ANALYZED_AT,
            instruments=[
                SnifferInstrumentResult(
                    instrument_id=eth.id,
                    symbol="ETHUSDT",
                    features={"return_5m": Decimal("0.05"), "return_1h": Decimal("0.03")},
                ),
                SnifferInstrumentResult(
                    instrument_id=btc.id, symbol="BTCUSDT", features={"return_5m": None}
                ),
            ],
        )
    )


# -- auth -----------------------------------------------------------------------------


async def test_sniffer_status_requires_authentication(client):
    response = await client.get("/api/sniffer/status")
    assert response.status_code == 401


async def test_sniffer_latest_requires_authentication(client):
    response = await client.get("/api/sniffer/latest")
    assert response.status_code == 401


async def test_sniffer_frame_requires_authentication(client):
    response = await client.get(f"/api/sniffer/frames/{FRAME_TIME.isoformat()}")
    assert response.status_code == 401


# -- /api/sniffer/status ---------------------------------------------------------------


async def test_status_reports_na_when_nothing_ever_analyzed(client, test_user):
    await _login(client, test_user)

    response = await client.get("/api/sniffer/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is None
    assert body["last_scan"] is None
    assert body["coins_analyzed"] is None


async def test_status_reports_ok_with_real_last_scan_and_coins_analyzed(
    client, test_user, db_session
):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    response = await client.get("/api/sniffer/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["coins_analyzed"] == 2
    assert body["last_scan"] is not None


async def test_status_never_writes_to_the_database(client, test_user, db_session):
    await _login(client, test_user)

    await client.get("/api/sniffer/status")

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert rows == []


# -- /api/sniffer/latest ----------------------------------------------------------------


async def test_latest_returns_404_when_nothing_analyzed_yet(client, test_user):
    await _login(client, test_user)

    response = await client.get("/api/sniffer/latest")

    assert response.status_code == 404


async def test_latest_returns_the_saved_result_sorted_by_symbol(client, test_user, db_session):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    response = await client.get("/api/sniffer/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["instruments_analyzed"] == 2
    symbols = [i["symbol"] for i in body["instruments"]]
    assert symbols == ["BTCUSDT", "ETHUSDT"]  # alphabetical, not insertion/value order


async def test_latest_represents_unavailable_as_null_never_zero_or_a_string(
    client, test_user, db_session
):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    response = await client.get("/api/sniffer/latest")

    body = response.json()
    btc = next(i for i in body["instruments"] if i["symbol"] == "BTCUSDT")
    assert btc["return_5m"] is None
    assert btc["return_1h"] is None  # older metric-only analysis remains readable
    eth = next(i for i in body["instruments"] if i["symbol"] == "ETHUSDT")
    assert Decimal(eth["return_5m"]) == Decimal("0.05")
    assert Decimal(eth["return_1h"]) == Decimal("0.03")


async def test_latest_never_writes_to_the_database(client, test_user, db_session):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    await client.get("/api/sniffer/latest")

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 3  # unchanged: two ETH metrics plus the legacy BTC metric


# -- /api/sniffer/frames/{frame_time} ----------------------------------------------------


async def test_frame_returns_404_for_an_unknown_frame_time(client, test_user, db_session):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    other = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
    response = await client.get(f"/api/sniffer/frames/{other.isoformat()}")

    assert response.status_code == 404


async def test_frame_returns_the_result_for_that_exact_frame_time(client, test_user, db_session):
    await _seed_analyzed_frame(db_session)
    await _login(client, test_user)

    response = await client.get(f"/api/sniffer/frames/{FRAME_TIME.isoformat()}")

    assert response.status_code == 200
    body = response.json()
    assert body["instruments_analyzed"] == 2
