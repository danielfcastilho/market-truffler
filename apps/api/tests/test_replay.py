from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.engine import FeatureEngine
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.research.replay import replay_frame
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
                "timeframe": "5m",
                "open_time": hour_start + timedelta(minutes=5 * i),
                "close_time": hour_start + timedelta(minutes=5 * i + 5) - timedelta(milliseconds=1),
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": Decimal(str(100 + i)),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
            for i in range(12)
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
            "open_time_5m": m5[instrument_id].open_time,
            "open_time_15m": m15[instrument_id].open_time,
            "open_time_1h": h1[instrument_id].open_time,
        }
        await frame_repo.finalize(frame_time, [member], now=datetime.now(UTC))


# -- production/research parity ----------------------------------------------------


async def test_replay_produces_byte_identical_sniffs_to_production_feature_engine(
    session_factory,
):
    """The concrete guarantee behind 'production feature math == research
    feature math': the exact same seeded frame, run once through
    `FeatureEngine.run` the way `Sniffer.analyze_frame` calls it internally,
    and once through `replay_frame`, must produce identical `features`
    dicts — same Decimal values, same None pattern, for every one of the
    23 current Sniffs.

    Deliberately compares against `FeatureEngine.run`'s own direct output,
    not against a row re-read from `sniffer_results` — that column is
    `NUMERIC(30,12)`, so a value round-tripped through it is rounded to 12
    decimal places, which would make an otherwise-irrelevant storage
    artifact look like a parity failure. Replay's whole point is to never
    inherit that DB-column precision constraint; comparing directly
    against `FeatureEngine.run`'s raw return value is what actually
    isolates "is the computation identical", which is the real invariant.
    A second test below confirms the full production path (through
    `Sniffer.analyze_frame`) also succeeds end-to-end.
    """
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start - timedelta(hours=1))
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
        production = await FeatureEngine().run(
            frame,
            CandleRepository(session),
            OpenInterestRepository(session),
            FundingRateRepository(session),
            analyzed_at=datetime.now(UTC),
        )

    async with session_factory() as session:
        replayed = await replay_frame(session, FRAME_TIME)

    assert replayed is not None
    production_features = {i.instrument_id: i.features for i in production.instruments}
    replayed_by_id = {i.instrument_id: i.features for i in replayed.sniffs.instruments}
    assert replayed_by_id == production_features
    assert set(replayed_by_id[btc]) == set(production_features[btc])  # exact same 23 keys
    # Parity over real values, not just a matching pattern of Nones.
    assert replayed_by_id[btc]["return_5m"] == Decimal(111) / Decimal(110) - 1
    assert replayed_by_id[btc]["return_1h"] == Decimal(0)
    assert replayed_by_id[btc]["rsi_14_5m"] is not None


async def test_production_analyze_frame_still_succeeds_end_to_end_for_the_same_seeded_frame(
    session_factory,
):
    """Sanity check alongside the parity test above: the full production
    path (through `Sniffer.analyze_frame`, persisting to `sniffer_results`)
    must not error for the same fixture — its rounded, persisted values are
    a separate concern (see the parity test's docstring), not a sign the
    production path itself is broken."""
    from app.repositories.sniffer_repository import SnifferRepository

    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start - timedelta(hours=1))
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(FRAME_TIME)

    async with session_factory() as session:
        production = await SnifferRepository(session).get_for_frame(FRAME_TIME)
    assert production is not None
    assert len(production.instruments) == 1


async def test_replay_is_deterministic_across_repeated_calls(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    async with session_factory() as session:
        first = await replay_frame(session, FRAME_TIME)
    async with session_factory() as session:
        second = await replay_frame(session, FRAME_TIME)

    assert first is not None
    assert second is not None
    first_by_id = {i.instrument_id: i.features for i in first.sniffs.instruments}
    second_by_id = {i.instrument_id: i.features for i in second.sniffs.instruments}
    assert first_by_id == second_by_id


async def test_replay_returns_none_for_a_frame_time_that_was_never_finalized(session_factory):
    async with session_factory() as session:
        result = await replay_frame(session, FRAME_TIME)
    assert result is None


async def test_replay_rejects_a_frame_time_off_a_5m_boundary(session_factory):
    """Only 5-minute boundaries are decision points; a legacy frame at e.g.
    14:03 is never silently replayed as if it were one."""
    async with session_factory() as session:
        with pytest.raises(ValueError, match="boundary"):
            await replay_frame(session, FRAME_TIME + timedelta(minutes=3))


async def test_replay_returns_none_for_a_still_building_frame(session_factory):
    """Mirrors `Sniffer.analyze_frame`'s own skip rule exactly: whatever
    Sniffer itself would decline to analyze, replay declines too."""
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC)
        )

    async with session_factory() as session:
        result = await replay_frame(session, FRAME_TIME)

    assert result is None


# -- no future-information leakage into replayed Sniffs --------------------------


async def test_future_candles_never_change_the_replayed_sniffs(session_factory):
    """Data that exists only *after* frame_time (exactly what
    `app.research.forward_returns` legitimately reads for labels) must
    never change a single replayed Sniff value — Sniffs and forward labels
    are structurally independent reads over the same frame."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start - timedelta(hours=1))
    await _seed_full_hour(session_factory, btc, hour_start)
    await _finalize_complete_frame(session_factory, btc, FRAME_TIME)

    async with session_factory() as session:
        before = await replay_frame(session, FRAME_TIME)

    # Plant wildly-different-looking "future" 5m candles from frame_time on
    # (the first one opens exactly at frame_time — still forming at T) and
    # everything derivable from them — exactly the kind of data
    # forward_returns.py is allowed to read, but Sniffer/replay never should.
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        await candle_repo.upsert_many(
            [
                {
                    "instrument_id": btc,
                    "timeframe": "5m",
                    "open_time": FRAME_TIME + timedelta(minutes=5 * i),
                    "close_time": FRAME_TIME
                    + timedelta(minutes=5 * i + 5)
                    - timedelta(milliseconds=1),
                    "open": Decimal("999999"),
                    "high": Decimal("999999"),
                    "low": Decimal("999999"),
                    "close": Decimal("999999"),
                    "volume": Decimal("1"),
                    "turnover": Decimal("1"),
                }
                for i in range(12)
            ]
        )
        await derive_higher_timeframes(
            candle_repo, btc, FRAME_TIME, FRAME_TIME + timedelta(hours=1)
        )

    async with session_factory() as session:
        after = await replay_frame(session, FRAME_TIME)

    assert before is not None
    assert after is not None
    before_features = {i.instrument_id: i.features for i in before.sniffs.instruments}
    after_features = {i.instrument_id: i.features for i in after.sniffs.instruments}
    assert before_features == after_features
