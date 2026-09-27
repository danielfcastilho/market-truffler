"""24h-specific Market Frame behavior: `MarketFrameMember.h24` is resolved
the same no-look-ahead way as the four gating timeframes (and 4h), but —
like 4h — never gates COMPLETE/PARTIAL (see
`app.domain.frame.MarketFrameMember.h4`/`h24` for why). These tests mirror
`test_frame_4h.py`'s coverage, specifically for 24h; the shared helpers
are intentionally duplicated in miniature here rather than imported, to
keep this file readable standalone.
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


DAY_1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
DAY_2 = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


async def test_frame_selects_only_the_latest_fully_closed_24h_candle(engine):
    """frame_time=2026-01-02 10:00 falls inside the still-forming
    01-02T00:00-01-03T00:00 bucket — only the earlier, fully-closed
    01-01T00:00-01-02T00:00 bucket may be selected."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    # A full 24h bucket (day 1) plus enough of day 2 (00:00-10:00) to
    # satisfy the gating timeframes at frame_time=10:00.
    await _seed_1m_range(session_factory, btc, DAY_1, 1440)
    await _seed_1m_range(session_factory, btc, DAY_2, 10 * 60 + 10)

    frame_time = DAY_2 + timedelta(hours=10, minutes=10)
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
    assert member.h24 is not None
    assert member.h24.open_time == DAY_1


async def test_currently_open_24h_bucket_never_enters_the_frame(engine):
    """Even with a partial day-2 bucket sitting in storage (more data than
    the still-forming bucket should ever expose), only the earlier closed
    bucket may be selected — the open one must never leak in."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_1m_range(session_factory, btc, DAY_1, 1440)
    # The current, still-forming day-2 bucket has plenty of 1m data too —
    # but it can never be a *closed* 24h candle at frame_time=10:10, so it
    # must never be materialized/selected.
    await _seed_1m_range(session_factory, btc, DAY_2, 10 * 60 + 10)

    frame_time = DAY_2 + timedelta(hours=10, minutes=10)
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    member = frame.members[0]
    assert member.h24 is not None
    assert member.h24.open_time == DAY_1  # not day 2 (still open)


async def test_missing_24h_context_does_not_prevent_completeness(engine):
    """24h is not a membership-gating timeframe (see
    MarketFrameMember.h24): an instrument with full 1m/5m/15m/1h context
    but no closed 24h bucket yet must still become a member of a COMPLETE
    frame, with h24 == None."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    # Only one hour of history — nowhere near a full 24h bucket.
    await _seed_1m_range(session_factory, btc, DAY_1 + timedelta(hours=13), 60)

    frame_time = DAY_1 + timedelta(hours=14)
    sync = _synchronizer(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            frame_time, expected_instrument_ids=[btc], now=datetime.now(UTC)
        )
    await sync._finalize_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)

    assert frame.status == FrameStatus.COMPLETE  # 24h absence never demotes this
    assert frame.available_instruments == 1
    member = frame.members[0]
    assert member.h24 is None  # honestly unavailable, never fabricated
    assert member.h1 is not None  # the gating timeframes are still real


async def test_older_finalized_frame_stays_no_lookahead_safe_after_newer_24h_data_arrives(engine):
    """A finalized frame is never re-finalized (M4 section 15/16): even if
    a duplicate finalize trigger fires after a much newer 24h bucket has
    since closed, the already-recorded h24 reference must not change."""
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_1m_range(session_factory, btc, DAY_1, 1440)
    await _seed_1m_range(session_factory, btc, DAY_2, 60)

    frame_time = DAY_2 + timedelta(hours=1)
    sync = _synchronizer(session_factory)
    await sync._build_frame(frame_time)

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)
    original_h24_open_time = frame.members[0].h24.open_time
    assert original_h24_open_time == DAY_1

    # A much newer, fully-closed 24h bucket arrives (day 2 itself closes)
    # — this must never retroactively alter the already-finalized frame.
    await _seed_1m_range(session_factory, btc, DAY_2, 1440)
    await sync._finalize_frame(frame_time)  # duplicate trigger — must be a no-op

    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(frame_time)
    assert frame.members[0].h24.open_time == original_h24_open_time
