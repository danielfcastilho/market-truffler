from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.instrument import Instrument
from app.models.sniffer import SnifferResult
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.sniffer_repository import SnifferRepository
from app.services.candle_aggregation import derive_higher_timeframes
from app.services.sniffer import Sniffer

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _add_instrument(session_factory, symbol: str) -> int:
    async with session_factory() as session:
        now = datetime.now(UTC)
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol.removesuffix("USDT"),
            quote_coin="USDT",
            is_active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_full_hour(session_factory, instrument_id: int, hour_start: datetime) -> None:
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": "1m",
                "open_time": hour_start + timedelta(minutes=i),
                "close_time": hour_start + timedelta(minutes=i + 1) - timedelta(microseconds=1),
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": Decimal(str(100 + i)),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
            for i in range(60)
        ]
        await candle_repo.upsert_many(rows)
        await derive_higher_timeframes(
            candle_repo, instrument_id, hour_start, hour_start + timedelta(hours=1)
        )


async def _finalize_complete_frame(
    session_factory, instrument_id: int, frame_time: datetime
) -> None:
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        frame_repo = FrameRepository(session)
        await frame_repo.create_building(
            frame_time, expected_instrument_ids=[instrument_id], now=datetime.now(UTC)
        )
        m1 = await candle_repo.fetch_latest_closed_per_instrument(
            "1m", [instrument_id], frame_time - timedelta(minutes=1)
        )
        m5 = await candle_repo.fetch_latest_closed_per_instrument(
            "5m", [instrument_id], frame_time - timedelta(minutes=5)
        )
        m15 = await candle_repo.fetch_latest_closed_per_instrument(
            "15m", [instrument_id], frame_time - timedelta(minutes=15)
        )
        h1 = await candle_repo.fetch_latest_closed_per_instrument(
            "1h", [instrument_id], frame_time - timedelta(minutes=60)
        )
        member = {
            "frame_time": frame_time,
            "instrument_id": instrument_id,
            "open_time_1m": m1[instrument_id].open_time,
            "open_time_5m": m5[instrument_id].open_time,
            "open_time_15m": m15[instrument_id].open_time,
            "open_time_1h": h1[instrument_id].open_time,
        }
        await frame_repo.finalize(frame_time, [member], now=datetime.now(UTC))


async def test_analyze_frame_persists_a_result_for_a_complete_frame(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start - timedelta(hours=1))
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)

    async with session_factory() as session:
        result = await SnifferRepository(session).get_for_frame(FRAME_TIME)
    assert result is not None
    assert len(result.instruments) == 1
    assert result.instruments[0].features["return_5m"] is not None
    assert result.instruments[0].features["return_1h"] == Decimal("0")


async def test_analyze_frame_only_analyzes_actual_frame_members_not_all_active_instruments(
    session_factory,
):
    """A second, active-but-not-a-member instrument (excluded from a
    PARTIAL frame) must never appear in Sniffer's persisted results for
    that frame — Sniffer must not reconstruct membership itself."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    eth = await _add_instrument(session_factory, "ETHUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    await _seed_full_hour(session_factory, eth, hour_start)  # ETH has full data too...

    # ...but the frame was only ever built/finalized for BTC (simulating a
    # PARTIAL frame where ETH was excluded at M4 finalization time).
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)

    async with session_factory() as session:
        result = await SnifferRepository(session).get_for_frame(FRAME_TIME)
    assert {i.symbol for i in result.instruments} == {"BTCUSDT"}


async def test_analyze_frame_skips_a_still_building_frame(session_factory):
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC)
        )

    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)  # must not raise

    async with session_factory() as session:
        rows = (await session.execute(select(SnifferResult))).scalars().all()
    assert rows == []


async def test_analyze_frame_skips_a_nonexistent_frame(session_factory):
    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)  # must not raise

    async with session_factory() as session:
        rows = (await session.execute(select(SnifferResult))).scalars().all()
    assert rows == []


async def test_analyze_frame_is_idempotent_on_retry(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)
    await sniffer.analyze_frame(FRAME_TIME)  # simulate a retry / duplicate trigger

    async with session_factory() as session:
        rows = (await session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 6  # one row per metric, not duplicated by retry
    assert {row.metric for row in rows} == {
        "return_5m",
        "return_1h",
        "rsi_14_5m",
        "rsi_14_15m",
        "rsi_14_1h",
        "rsi_14_4h",
    }


async def test_on_frame_finalized_swallows_analysis_failures(session_factory, monkeypatch):
    """A Sniffer failure must never propagate to the caller (FrameSynchronizer)."""
    sniffer = Sniffer(session_factory)

    async def _boom(frame_time):
        raise RuntimeError("feature engine exploded")

    monkeypatch.setattr(sniffer, "analyze_frame", _boom)

    await sniffer.on_frame_finalized(FRAME_TIME)  # must not raise
