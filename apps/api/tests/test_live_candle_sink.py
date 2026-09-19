from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.market import ClosedCandle
from app.models.candle import Candle
from app.models.instrument import Instrument
from app.services.live_candle_sink import PersistingCandleSink


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


def _candle(symbol: str, open_time: datetime) -> ClosedCandle:
    return ClosedCandle(
        exchange="bybit",
        symbol=symbol,
        timeframe="1m",
        open_time=open_time,
        close_time=open_time.replace(second=59, microsecond=999000),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        turnover=Decimal("1000"),
    )


async def test_persists_a_live_candle_for_a_known_instrument(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Instrument(
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
        )
        await session.commit()

    sink = PersistingCandleSink(session_factory)
    await sink(_candle("BTCUSDT", datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))

    async with session_factory() as session:
        stored = (await session.execute(select(Candle))).scalars().all()
        assert len(stored) == 1
        assert stored[0].timeframe == "1m"


async def test_ignores_a_candle_for_an_unknown_symbol_without_raising(session_factory):
    sink = PersistingCandleSink(session_factory)

    await sink(_candle("UNKNOWNUSDT", datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))  # must not raise

    async with session_factory() as session:
        stored = (await session.execute(select(Candle))).scalars().all()
        assert stored == []


async def test_mapping_refresh_picks_up_instruments_registered_after_first_call(session_factory):
    sink = PersistingCandleSink(session_factory, mapping_refresh_seconds=0)

    await sink(_candle("BTCUSDT", datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    async with session_factory() as session:
        assert (await session.execute(select(Candle))).scalars().all() == []

    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Instrument(
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
        )
        await session.commit()

    # mapping_refresh_seconds=0 means the next call always re-fetches.
    await sink(_candle("BTCUSDT", datetime(2026, 1, 1, 12, 1, tzinfo=UTC)))
    async with session_factory() as session:
        assert len((await session.execute(select(Candle))).scalars().all()) == 1
