from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository


@pytest.fixture
async def instrument_id(db_session) -> int:
    now = datetime.now(UTC)
    row = Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
        history_target_start=now,
        history_synced_from=now,
        history_synced_through=now,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


def _row(instrument_id: int, minute: int, close: str = "100.0") -> dict:
    open_time = datetime(2026, 1, 1, 0, minute, tzinfo=UTC)
    return {
        "instrument_id": instrument_id,
        "timeframe": "1m",
        "open_time": open_time,
        "close_time": open_time.replace(second=59, microsecond=999000),
        "open": Decimal("100.0"),
        "high": Decimal("101.0"),
        "low": Decimal("99.0"),
        "close": Decimal(close),
        "volume": Decimal("10.0"),
        "turnover": Decimal("1000.0"),
    }


async def test_closed_candle_persists_correctly(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1
    assert stored[0].instrument_id == instrument_id
    assert stored[0].timeframe == "1m"
    assert stored[0].close == Decimal("100.0")


async def test_duplicate_ingestion_is_idempotent(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])
    await repo.upsert_many([_row(instrument_id, 0)])  # exact duplicate

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1


async def test_candle_uniqueness_enforced_via_upsert_correction(db_session, instrument_id):
    """A second write for the same (instrument, timeframe, open_time) key
    overwrites rather than duplicating — the composite primary key enforces
    uniqueness, and the upsert path treats a differing value as a
    correction (M3 section 15)."""
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0, close="100.0")])
    await repo.upsert_many([_row(instrument_id, 0, close="105.5")])  # corrected value

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1
    assert stored[0].close == Decimal("105.5")


async def test_upsert_many_batches_multiple_rows_in_one_call(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, m) for m in range(5)])

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 5


async def test_upsert_many_is_a_noop_for_an_empty_batch(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([])  # must not raise

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert stored == []


async def test_fetch_range_returns_only_rows_within_bounds_ordered(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, m) for m in range(10)])

    rows = await repo.fetch_range(
        instrument_id,
        "1m",
        datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    )

    assert [r.open_time.minute for r in rows] == [2, 3, 4]
