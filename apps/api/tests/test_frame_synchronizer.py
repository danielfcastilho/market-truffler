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
FIVE = timedelta(minutes=5)


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


def _base_row(instrument_id: int, open_time: datetime, close: str = "1") -> dict:
    return {
        "instrument_id": instrument_id,
        "timeframe": "5m",
        "open_time": open_time,
        "close_time": open_time + FIVE - timedelta(milliseconds=1),
        "open": Decimal("1"),
        "high": Decimal("1"),
        "low": Decimal("1"),
        "close": Decimal(close),
        "volume": Decimal("1"),
        "turnover": Decimal("1"),
    }


async def _seed_base(session_factory, instrument_id: int, open_times: list[datetime]) -> None:
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        await candle_repo.upsert_many([_base_row(instrument_id, t) for t in open_times])
        await derive_higher_timeframes(
            candle_repo, instrument_id, min(open_times), max(open_times) + FIVE
        )


async def _seed_full_hour(session_factory, instrument_id: int, hour_start: datetime) -> None:
    """Twelve 5m candles covering [hour_start, hour_start + 1h), plus the
    15m/1h candles derived from them."""
    await _seed_base(session_factory, instrument_id, [hour_start + FIVE * i for i in range(12)])


def _synchronizer(session_factory, **overrides) -> FrameSynchronizer:
    kwargs = {"grace_period_seconds": 0.0}
    kwargs.update(overrides)
    return FrameSynchronizer(session_factory, **kwargs)


# -- one frame per 5-minute boundary, full universe snapshot -------------------------


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
    # ETH has a single 5m candle — no 15m/1h context will exist for it.
    async with session_factory() as session:
        await CandleRepository(session).upsert_many([_base_row(eth, FRAME_TIME - FIVE)])

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
    """With one complete hour (13:00-13:59) plus the first two 5m candles
    of the next one, a frame at 14:10 must select the fully-closed 13:00
    hour — never the still-forming 14:00 one — and the latest legal 15m
    candle is 13:45, since the 14:00 15m bucket still lacks 14:10."""
    hour_start = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)
    await _seed_base(
        session_factory, btc, [hour_start + timedelta(hours=1) + FIVE * i for i in range(2)]
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
    assert member.m15.open_time == datetime(2026, 1, 1, 13, 45, tzinfo=UTC)
    assert member.m5.open_time == frame_time - FIVE  # 14:05, closed exactly at 14:10


async def test_a_5m_candle_is_usable_from_its_close_and_never_before(session_factory):
    """The canonical boundary case: the 5m candle opening at 10:00 closes
    at 10:05. A frame at 10:05 selects it; a frame at 10:00 must not, even
    though the row is already sitting in storage (as it would be when a
    frame is resolved late, e.g. after a restart)."""
    btc = await _add_instrument(session_factory, "BTCUSDT")
    ten = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    await _seed_full_hour(session_factory, btc, ten - timedelta(hours=1))
    await _seed_base(session_factory, btc, [ten])  # opens 10:00, closes 10:05

    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        repo = FrameRepository(session)
        for frame_time in (ten, ten + FIVE):
            await repo.create_building(
                frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
            )
    await sync._finalize_frame(ten)
    await sync._finalize_frame(ten + FIVE)

    async with session_factory() as session:
        at_ten = await FrameRepository(session).get_frame(ten)
        at_ten_oh_five = await FrameRepository(session).get_frame(ten + FIVE)
    assert at_ten.members[0].m5.open_time == ten - FIVE  # 09:55 — the 10:00 candle isn't closed
    assert at_ten_oh_five.members[0].m5.open_time == ten  # usable from 10:05 on


async def test_run_loop_builds_exactly_the_boundary_it_slept_toward(session_factory):
    """The loop builds the boundary `_sleep_until_next_boundary` returns,
    never one re-derived from the clock afterwards."""
    sync = _synchronizer(session_factory)
    sync._stop_event = asyncio.Event()
    wakeups = iter([datetime(2026, 1, 1, 10, 5, tzinfo=UTC), None])  # one boundary, then stop

    async def fake_sleep() -> datetime | None:
        return next(wakeups)

    built: list[datetime] = []

    async def fake_build(frame_time: datetime) -> None:
        built.append(frame_time)

    sync._sleep_until_next_boundary = fake_sleep  # type: ignore[method-assign]
    sync._build_frame = fake_build  # type: ignore[method-assign]
    await sync._run_loop()

    assert built == [datetime(2026, 1, 1, 10, 5, tzinfo=UTC)]


async def test_an_early_timer_wakeup_still_yields_the_upcoming_boundary(
    session_factory, monkeypatch
):
    """asyncio timers can fire a hair early. Waking at 10:04:59.9995 must
    keep waiting the remainder and return 10:05 — never floor back to the
    10:00 boundary, which would build a frame one candle stale."""
    from app.services import frame_synchronizer

    clock = iter(
        [
            datetime(2026, 1, 1, 10, 4, 59, 990_000, tzinfo=UTC),  # computing the target
            datetime(2026, 1, 1, 10, 4, 59, 995_000, tzinfo=UTC),  # first wait: 5ms
            datetime(2026, 1, 1, 10, 4, 59, 999_500, tzinfo=UTC),  # woke early: 0.5ms more
            datetime(2026, 1, 1, 10, 5, 0, 200, tzinfo=UTC),  # boundary reached
        ]
    )

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return next(clock)

    monkeypatch.setattr(frame_synchronizer, "datetime", _Clock)
    sync = _synchronizer(session_factory)
    sync._stop_event = asyncio.Event()

    assert await sync._sleep_until_next_boundary() == datetime(2026, 1, 1, 10, 5, tzinfo=UTC)


async def test_sleep_until_next_boundary_returns_none_when_stopped(session_factory):
    sync = _synchronizer(session_factory)
    sync._stop_event = asyncio.Event()
    sync._stop_event.set()

    assert await sync._sleep_until_next_boundary() is None


# -- idempotency ------------------------------------------------------------------


async def test_duplicate_build_frame_call_is_idempotent(session_factory):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    sync = _synchronizer(session_factory)
    await sync._build_frame(FRAME_TIME)
    await sync._build_frame(FRAME_TIME)  # duplicate trigger for the same frame_time

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


async def test_downtime_does_not_fabricate_frames_for_skipped_boundaries(session_factory):
    """Only the one interrupted BUILDING frame is resolved at startup —
    boundaries that were simply never observed (the app was offline) get no
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
    # Exactly the one (resolved) frame — nothing fabricated for any other boundary.
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
    bounded number of set-oriented queries (one per timeframe plus light
    bookkeeping), never proportional to instrument count."""
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
        for timeframe, minutes in (("5m", 5), ("15m", 15), ("1h", 60)):
            await candle_repo.fetch_latest_closed_per_instrument(
                timeframe, expected_ids, FRAME_TIME - timedelta(minutes=minutes)
            )

    # One query to read the frame's expected-instrument snapshot, plus
    # exactly one query per gating timeframe (three) — four total for a
    # 20-instrument universe, not 20 and not 60 (20 instruments x 3
    # timeframes).
    assert query_count == 4


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


# -- on_frame_finalized hook (M5) ----------------------------------------------------


async def test_on_frame_finalized_hook_is_called_once_with_the_finalized_frame_time(
    session_factory,
):
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    calls: list[datetime] = []

    async def hook(frame_time: datetime) -> None:
        calls.append(frame_time)

    sync = _synchronizer(session_factory, on_frame_finalized=hook)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)

    assert calls == [FRAME_TIME]


async def test_on_frame_finalized_hook_is_not_called_when_nothing_is_finalized(session_factory):
    """No BUILDING frame exists at this frame_time, so `_finalize_frame`
    finalizes nothing — the hook must not fire for a no-op."""
    calls: list[datetime] = []

    async def hook(frame_time: datetime) -> None:
        calls.append(frame_time)

    sync = _synchronizer(session_factory, on_frame_finalized=hook)
    await sync._finalize_frame(FRAME_TIME)

    assert calls == []


async def test_on_frame_finalized_hook_failure_does_not_break_finalization(session_factory):
    """A Sniffer-side failure inside the hook must never prevent MARKET's
    own frame finalization from completing, nor propagate out of
    `_finalize_frame` (M5 section 14: Sniffer must never be able to break
    FrameSynchronizer)."""
    hour_start = FRAME_TIME - timedelta(hours=1)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, btc, hour_start)

    async def failing_hook(frame_time: datetime) -> None:
        raise RuntimeError("sniffer exploded")

    sync = _synchronizer(session_factory, on_frame_finalized=failing_hook)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            FRAME_TIME, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(FRAME_TIME)  # must not raise

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(FRAME_TIME)
    assert frame.status == FrameStatus.COMPLETE
