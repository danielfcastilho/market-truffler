"""4h-specific Market Frame behavior: `MarketFrameMember.h4` is resolved the
same no-look-ahead way as the four gating timeframes, but — unlike them —
never gates COMPLETE/PARTIAL (see `app.domain.frame.MarketFrameMember.h4`
for why). These tests extend `test_frame_synchronizer.py`'s coverage
specifically for 4h; the shared helpers are intentionally duplicated in
miniature here rather than imported, to keep this file readable standalone.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.frame import FrameStatus
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.services.candle_aggregation import derive_higher_timeframes
from app.services.frame_synchronizer import FrameSynchronizer


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


async def _seed_1m_range(
    session_factory, instrument_id: int, start: datetime, minutes: int
) -> None:
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": "1m",
                "open_time": start + timedelta(minutes=i),
                "close_time": start + timedelta(minutes=i + 1) - timedelta(microseconds=1),
                "open": Decimal("1"),
                "high": Decimal("1"),
                "low": Decimal("1"),
                "close": Decimal("1"),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
            for i in range(minutes)
        ]
        await candle_repo.upsert_many(rows)
        await derive_higher_timeframes(
            candle_repo, instrument_id, start, start + timedelta(minutes=minutes)
        )


def _synchronizer(session_factory) -> FrameSynchronizer:
    return FrameSynchronizer(session_factory, grace_period_seconds=0.0)


async def test_frame_selects_only_the_latest_fully_closed_4h_candle(engine):
    """frame_time=14:10 falls inside the still-forming 12:00-16:00 bucket —
    only the earlier, fully-closed 08:00-12:00 bucket may be selected."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    # A full 4h bucket (08:00-12:00) plus enough of the current hour
    # (13:00-14:10) to satisfy the gating timeframes at frame_time=14:10.
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), 240)
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 13, 0, tzinfo=UTC), 70)

    frame_time = datetime(2026, 1, 1, 14, 10, tzinfo=UTC)
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    assert frame.status == FrameStatus.COMPLETE
    member = frame.members[0]
    assert member.h4 is not None
    assert member.h4.open_time == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)


async def test_currently_open_4h_bucket_never_enters_the_frame(engine):
    """Even with a partial 12:00-16:00 bucket sitting in storage (more data
    than the still-forming bucket should ever expose), only the earlier
    closed bucket may be selected — the open one must never leak in."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), 240)
    # The current, still-forming 12:00-16:00 bucket has plenty of 1m data
    # too (130 minutes: 12:00-14:10) — but it can never be a *closed* 4h
    # candle at frame_time=14:10, so it must never be materialized/selected.
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 12, 0, tzinfo=UTC), 130)

    frame_time = datetime(2026, 1, 1, 14, 10, tzinfo=UTC)
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    member = frame.members[0]
    assert member.h4 is not None
    assert member.h4.open_time == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)  # not 12:00 (still open)


async def test_missing_4h_context_does_not_prevent_completeness(engine):
    """4h is not a membership-gating timeframe (see MarketFrameMember.h4):
    an instrument with full 1m/5m/15m/1h context but no closed 4h bucket
    yet must still become a member of a COMPLETE frame, with h4 == None."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    # Only one hour of history — nowhere near a full 4h bucket.
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 13, 0, tzinfo=UTC), 60)

    frame_time = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    assert frame.status == FrameStatus.COMPLETE  # 4h absence never demotes this
    assert frame.available_instruments == 1
    member = frame.members[0]
    assert member.h4 is None  # honestly unavailable, never fabricated
    assert member.h1 is not None  # the gating timeframes are still real


async def test_older_finalized_frame_stays_no_lookahead_safe_after_newer_4h_data_arrives(engine):
    """A finalized frame is never re-finalized (M4 section 15/16): even if
    a duplicate finalize trigger fires after a much newer 4h bucket has
    since closed, the already-recorded h4 reference must not change."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), 240)
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 13, 0, tzinfo=UTC), 60)

    frame_time = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    sync = _synchronizer(session_factory)
    await sync._build_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)
    original_h4_open_time = frame.members[0].h4.open_time
    assert original_h4_open_time == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)

    # A much newer, fully-closed 4h bucket arrives (e.g. hours later, once
    # 12:00-16:00 has itself closed) — this must never retroactively alter
    # the already-finalized frame at 14:00.
    await _seed_1m_range(session_factory, btc, datetime(2026, 1, 1, 12, 0, tzinfo=UTC), 240)
    await sync._finalize_frame(frame_time)  # duplicate trigger — must be a no-op

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)
    assert frame.members[0].h4.open_time == original_h4_open_time
