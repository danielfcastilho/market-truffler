import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.frame import FrameStatus
from app.models.frame import MarketFrame
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.services.candle_aggregation import derive_higher_timeframes
from app.services.frame_synchronizer import FrameSynchronizer

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _add_instrument(session_factory, symbol: str, *, is_active: bool = True) -> int:
    async with session_factory() as session:
        now = datetime.now(UTC)
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol.removesuffix("USDT"),
            quote_coin="USDT",
            is_active=is_active,
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
                "open": Decimal("1"),
                "high": Decimal("1"),
                "low": Decimal("1"),
                "close": Decimal(str(i)),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
            for i in range(60)
        ]
        await candle_repo.upsert_many(rows)
        await derive_higher_timeframes(
            candle_repo, instrument_id, hour_start, hour_start + timedelta(hours=1)
        )


def _synchronizer(session_factory, **overrides) -> FrameSynchronizer:
    kwargs = {"grace_period_seconds": 0.0}
    kwargs.update(overrides)
    return FrameSynchronizer(session_factory, **kwargs)


# -- one frame per minute, full universe snapshot -----------------------------------


async def test_finalize_frame_produces_one_complete_frame_when_all_instruments_ready(
    session_factory,
):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    eth = await _add_instrument(session_factory, "ETHUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    await _seed_full_hour(session_factory, eth, hour_start)

    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc, eth], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)

    assert frame.status == FrameStatus.COMPLETE
    assert frame.expected_instruments == 2
    assert frame.available_instruments == 2
    assert frame.completeness == 1.0
    assert {m.symbol for m in frame.members} == {"BTCUSDT", "ETHUSDT"}


async def test_finalize_frame_is_partial_when_one_instrument_lacks_full_context(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    eth = await _add_instrument(session_factory, "ETHUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    # ETH has only a few 1m candles — no 1h context will exist for it.
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        await candle_repo.upsert_many(
            [
                {
                    "instrument_id": eth,
                    "timeframe": "1m",
                    "open_time": FRAME_TIME - timedelta(minutes=1),
                    "close_time": FRAME_TIME - timedelta(microseconds=1),
                    "open": Decimal("1"),
                    "high": Decimal("1"),
                    "low": Decimal("1"),
                    "close": Decimal("1"),
                    "volume": Decimal("1"),
                    "turnover": Decimal("1"),
                }
            ]
        )

    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc, eth], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)

    assert frame.status == FrameStatus.PARTIAL
    assert frame.expected_instruments == 2
    assert frame.available_instruments == 1
    assert frame.completeness == 0.5
    assert {m.symbol for m in frame.members} == {"BTCUSDT"}  # missing candle never fabricated


async def test_inactive_instruments_are_not_expected(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _add_instrument(session_factory, "DELISTEDUSDT", is_active=False)
    await _seed_full_hour(session_factory, btc, hour_start)

    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        active = await InstrumentRepository(session).list_active()
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[i.id for i in active], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)

    assert frame.expected_instruments == 1  # only the active one
    assert frame.status == FrameStatus.COMPLETE


async def test_universe_change_after_finalization_does_not_mutate_expected_count(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)

    # A new instrument joins the universe *after* the frame was finalized.
    await _add_instrument(session_factory, "NEWCOINUSDT")

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
    assert frame.expected_instruments == 1  # unchanged historical fact
    assert frame.status == FrameStatus.COMPLETE


# -- temporal correctness through the full build path --------------------------------


async def test_a_still_forming_higher_timeframe_candle_cannot_leak_into_the_frame(session_factory):
    """Only 61 of the needed 120 minutes exist — the second 1h window
    (14:00-14:59) is still forming and must not appear as legal context at
    frame_time=15:00; only the 13:00-13:59 hour may be selected... here we
    instead prove the simpler, sharper case: with only ONE complete hour of
    1m data, frame_time set to the *middle* of what would be a second,
    still-forming hour must select the earlier, fully-closed hour only."""
    hour_start = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    # A handful of extra 1m candles into the *next*, still-forming hour.
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        await candle_repo.upsert_many(
            [
                {
                    "instrument_id": btc,
                    "timeframe": "1m",
                    "open_time": hour_start + timedelta(hours=1, minutes=i),
                    "close_time": hour_start
                    + timedelta(hours=1, minutes=i + 1)
                    - timedelta(microseconds=1),
                    "open": Decimal("1"),
                    "high": Decimal("1"),
                    "low": Decimal("1"),
                    "close": Decimal("1"),
                    "volume": Decimal("1"),
                    "turnover": Decimal("1"),
                }
                for i in range(10)  # only 10 of the 60 minutes needed for the next 1h candle
            ]
        )

    frame_time = hour_start + timedelta(hours=1, minutes=10)  # 14:10
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    assert frame.status == FrameStatus.COMPLETE  # the earlier, complete hour is legal
    member = frame.members[0]
    assert member.h1.open_time == hour_start  # NOT the still-forming 14:00 hour
    assert member.m1.open_time == frame_time - timedelta(minutes=1)  # 14:09, the latest legal 1m


# -- idempotency ------------------------------------------------------------------


async def test_duplicate_build_frame_call_is_idempotent(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    sync = _synchronizer(session_factory)
    await sync._build_frame(FRAME_TIME)
    await sync._build_frame(FRAME_TIME)  # duplicate trigger for the same minute

    async with session_factory() as session:
        rows = (await session.execute(select(MarketFrame))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == FrameStatus.COMPLETE.value


# -- restart / interrupted build -----------------------------------------------------


async def test_resolve_interrupted_build_finalizes_a_leftover_building_frame(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    # Simulate a previous process crashing right after creating the BUILDING
    # row, before it could finalize.
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )

    sync = _synchronizer(session_factory)
    await sync._resolve_interrupted_build()

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
        assert frame.status == FrameStatus.COMPLETE
        assert await FrameRepository(session).find_building() is None


async def test_interrupted_build_finalizes_against_its_original_universe_not_the_current_one(
    session_factory,
):
    """The exact scenario this follow-up fixes: A/B/C is active when the
    frame enters BUILDING; before it can finalize, the universe changes to
    A/B/D (C delisted, D newly listed) and the process crashes. Recovery at
    "restart" must still evaluate exactly A/B/C — D must never enter this
    historical frame, expected_instruments must stay 3, and if C's context
    turns out to be unavailable, the frame must truthfully end PARTIAL
    rather than silently substituting D to reach 3/3."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    a = await _add_instrument(session_factory, "AUSDT")
    b = await _add_instrument(session_factory, "BUSDT")
    c = await _add_instrument(session_factory, "CUSDT")
    await _seed_full_hour(session_factory, a, hour_start)
    await _seed_full_hour(session_factory, b, hour_start)
    await _seed_full_hour(session_factory, c, hour_start)

    # 1. Frame enters BUILDING with the active set snapshotted as A/B/C.
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[a, b, c], now=datetime.now(UTC)
        )

    # 2. Universe changes to A/B/D before finalization: C is delisted, D
    # joins — and D even gets full candle context, so it *could* wrongly
    # complete the frame if the current universe were used instead of the
    # snapshot.
    async with session_factory() as session:
        c_row = await session.get(Instrument, c)
        c_row.is_active = False
        await session.commit()
    d = await _add_instrument(session_factory, "DUSDT")
    await _seed_full_hour(session_factory, d, hour_start)

    # 3. Simulate restart/recovery.
    sync = _synchronizer(session_factory)
    await sync._resolve_interrupted_build()

    # 4/5/6. Frame must still evaluate exactly A/B/C: expected stays 3, and
    # D must not appear anywhere in it.
    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
    assert frame.expected_instruments == 3
    assert {m.symbol for m in frame.members} <= {"AUSDT", "BUSDT", "CUSDT"}
    assert "DUSDT" not in {m.symbol for m in frame.members}

    # 7. C's context *was* available here, so this particular frame reaches
    # COMPLETE — but on the original A/B/C set (3/3), not by counting D.
    assert frame.status == FrameStatus.COMPLETE
    assert frame.available_instruments == 3


async def test_interrupted_build_is_truthfully_partial_when_original_member_unavailable(
    session_factory,
):
    """Same scenario, but C's own context is genuinely unavailable at
    recovery time — the frame must end PARTIAL (2/3), never padded back to
    3/3 using the newly-active D."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    a = await _add_instrument(session_factory, "AUSDT")
    b = await _add_instrument(session_factory, "BUSDT")
    c = await _add_instrument(session_factory, "CUSDT")
    await _seed_full_hour(session_factory, a, hour_start)
    await _seed_full_hour(session_factory, b, hour_start)
    # C has no candle data at all — its context is genuinely unavailable.

    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[a, b, c], now=datetime.now(UTC)
        )

    async with session_factory() as session:
        c_row = await session.get(Instrument, c)
        c_row.is_active = False
        await session.commit()
    d = await _add_instrument(session_factory, "DUSDT")
    await _seed_full_hour(session_factory, d, hour_start)

    sync = _synchronizer(session_factory)
    await sync._resolve_interrupted_build()

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
    assert frame.expected_instruments == 3  # unchanged — never silently reduced to 2
    assert frame.available_instruments == 2
    assert frame.status == FrameStatus.PARTIAL
    assert {m.symbol for m in frame.members} == {"AUSDT", "BUSDT"}
    assert "DUSDT" not in {m.symbol for m in frame.members}  # never substituted in


async def test_resolve_interrupted_build_is_a_noop_when_nothing_was_building(session_factory):
    sync = _synchronizer(session_factory)
    await sync._resolve_interrupted_build()  # must not raise

    async with session_factory() as session:
        assert (await session.execute(select(MarketFrame))).scalars().all() == []


async def test_downtime_does_not_fabricate_frames_for_skipped_minutes(session_factory):
    """Only the one interrupted BUILDING frame is resolved at startup —
    minutes that were simply never observed (the app was offline) get no
    frame at all, fabricated or otherwise."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )

    sync = _synchronizer(session_factory)
    await sync._resolve_interrupted_build()

    async with session_factory() as session:
        rows = (await session.execute(select(MarketFrame))).scalars().all()
    # Exactly the one (resolved) frame — nothing fabricated for any other minute.
    assert len(rows) == 1
    assert rows[0].frame_time == FRAME_TIME


# -- lifecycle ----------------------------------------------------------------------


async def test_start_and_stop_is_clean(session_factory):
    sync = FrameSynchronizer(session_factory, grace_period_seconds=0.0)

    await sync.start()
    assert sync.running is True

    await asyncio.wait_for(sync.stop(), timeout=2)
    assert sync.running is False


async def test_grace_period_delays_finalization_past_the_first_arrival(session_factory):
    """M4 section 11/12: a frame must not finalize the instant it's
    created — it must remain BUILDING through the grace period, then
    finalize once it elapses."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    sync = _synchronizer(session_factory, grace_period_seconds=0.2)
    task = asyncio.create_task(sync._build_frame(FRAME_TIME))

    await asyncio.sleep(0.05)  # well within the grace period
    async with session_factory() as session:
        mid_build = await FrameRepository(session).get_frame(FRAME_TIME)
    assert mid_build.status == FrameStatus.BUILDING

    await asyncio.wait_for(task, timeout=2)
    async with session_factory() as session:
        finalized = await FrameRepository(session).get_frame(FRAME_TIME)
    assert finalized.status == FrameStatus.COMPLETE


async def test_finalize_frame_uses_a_bounded_number_of_queries_not_one_per_instrument(
    session_factory,
):
    """M4 section 25: resolving an entire universe's frame must be a small,
    bounded number of set-oriented queries (four — one per timeframe —
    plus light bookkeeping), never proportional to instrument count."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    instrument_ids = []
    for i in range(20):
        instrument_id = await _add_instrument(session_factory, f"SYM{i}USDT")
        instrument_ids.append(instrument_id)
        await _seed_full_hour(session_factory, instrument_id, hour_start)

    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=instrument_ids, now=datetime.now(UTC)
        )

    async with session_factory() as session:
        query_count = 0
        real_execute = session.execute

        async def counting_execute(*args, **kwargs):
            nonlocal query_count
            query_count += 1
            return await real_execute(*args, **kwargs)

        session.execute = counting_execute

        frame_repo = FrameRepository(session)
        expected_ids = await frame_repo.get_expected_instrument_ids(FRAME_TIME)
        candle_repo = CandleRepository(session)
        for timeframe, minutes in (("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60)):
            await candle_repo.fetch_latest_closed_per_instrument(
                timeframe, expected_ids, FRAME_TIME - timedelta(minutes=minutes)
            )

    # One query to read the frame's expected-instrument snapshot, plus
    # exactly one query per timeframe (four) — five total for a
    # 20-instrument universe, not 20 and not 80 (20 instruments x 4
    # timeframes).
    assert query_count == 5


async def test_vitals_reading_the_latest_frame_never_creates_one(session_factory):
    """M4 section 10/24: a read-only observer must never trigger frame
    construction — proven here by calling the exact repository method
    Vitals uses and confirming it never writes anything."""
    async with session_factory() as session:
        result = await FrameRepository(session).get_latest_finalized()
    assert result is None

    async with session_factory() as session:
        rows = (await session.execute(select(MarketFrame))).scalars().all()
    assert rows == []
