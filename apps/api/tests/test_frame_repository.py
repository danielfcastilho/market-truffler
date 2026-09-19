from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.frame import FrameStatus
from app.models.frame import MarketFrame, MarketFrameMember
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.services.candle_aggregation import derive_higher_timeframes

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


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
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


async def _seed_full_hour_of_1m(db_session, instrument_id: int, hour_start: datetime) -> None:
    """Seed 60 contiguous 1m candles and derive 5m/15m/1h from them, so
    `hour_start + 1h` (== FRAME_TIME in these tests) has legal context on
    every configured timeframe."""
    candle_repo = CandleRepository(db_session)
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


async def _member_rows_for(db_session, instrument_id: int, frame_time: datetime) -> list[dict]:
    candle_repo = CandleRepository(db_session)
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
    if instrument_id not in m1 or instrument_id not in m5 or instrument_id not in m15:
        return []
    if instrument_id not in h1:
        return []
    return [
        {
            "frame_time": frame_time,
            "instrument_id": instrument_id,
            "open_time_1m": m1[instrument_id].open_time,
            "open_time_5m": m5[instrument_id].open_time,
            "open_time_15m": m15[instrument_id].open_time,
            "open_time_1h": h1[instrument_id].open_time,
        }
    ]


# -- create_building / idempotency -------------------------------------------------


async def test_create_building_inserts_a_building_row(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(
        FRAME_TIME, expected_instrument_ids=[1, 2, 3, 4, 5], now=datetime.now(UTC)
    )

    row = (await db_session.execute(select(MarketFrame))).scalars().one()
    assert row.status == FrameStatus.BUILDING.value
    assert row.expected_instrument_ids == [1, 2, 3, 4, 5]
    assert row.expected_instruments == 5
    assert row.available_instruments == 0
    assert row.finalized_at is None


async def test_create_building_is_idempotent(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(
        FRAME_TIME, expected_instrument_ids=[1, 2, 3, 4, 5], now=datetime.now(UTC)
    )
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[10, 20], now=datetime.now(UTC))

    rows = (await db_session.execute(select(MarketFrame))).scalars().all()
    assert len(rows) == 1
    assert rows[0].expected_instruments == 5  # first write wins, never silently changed
    assert rows[0].expected_instrument_ids == [1, 2, 3, 4, 5]


# -- finalize / immutability --------------------------------------------------------


async def test_finalize_transitions_to_complete_when_all_expected_are_available(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))

    finalized = await repo.finalize(
        FRAME_TIME,
        [
            {
                "frame_time": FRAME_TIME,
                "instrument_id": 1,
                "open_time_1m": FRAME_TIME,
                "open_time_5m": FRAME_TIME,
                "open_time_15m": FRAME_TIME,
                "open_time_1h": FRAME_TIME,
            }
        ],
        now=datetime.now(UTC),
    )

    assert finalized is True
    row = (await db_session.execute(select(MarketFrame))).scalars().one()
    assert row.status == FrameStatus.COMPLETE.value
    assert row.available_instruments == 1
    assert row.finalized_at is not None


async def test_finalize_transitions_to_partial_when_fewer_available_than_expected(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1, 2], now=datetime.now(UTC))

    await repo.finalize(
        FRAME_TIME,
        [
            {
                "frame_time": FRAME_TIME,
                "instrument_id": 1,
                "open_time_1m": FRAME_TIME,
                "open_time_5m": FRAME_TIME,
                "open_time_15m": FRAME_TIME,
                "open_time_1h": FRAME_TIME,
            }
        ],
        now=datetime.now(UTC),
    )

    row = (await db_session.execute(select(MarketFrame))).scalars().one()
    assert row.status == FrameStatus.PARTIAL.value
    assert row.expected_instruments == 2
    assert row.available_instruments == 1  # never silently reduced to match


async def test_finalize_with_zero_available_members_is_partial_not_fabricated(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1, 2, 3], now=datetime.now(UTC))

    await repo.finalize(FRAME_TIME, [], now=datetime.now(UTC))

    row = (await db_session.execute(select(MarketFrame))).scalars().one()
    assert row.status == FrameStatus.PARTIAL.value
    assert row.available_instruments == 0


async def test_finalize_is_idempotent_and_never_rewrites_a_terminal_frame(db_session):
    """M4 section 15/16: late data must not silently rewrite an already
    PARTIAL frame into COMPLETE."""
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1, 2], now=datetime.now(UTC))
    await repo.finalize(
        FRAME_TIME,
        [
            {
                "frame_time": FRAME_TIME,
                "instrument_id": 1,
                "open_time_1m": FRAME_TIME,
                "open_time_5m": FRAME_TIME,
                "open_time_15m": FRAME_TIME,
                "open_time_1h": FRAME_TIME,
            }
        ],
        now=datetime.now(UTC),
    )

    # Late data "recovers" the second instrument — attempting to finalize
    # again with both members present must NOT flip PARTIAL -> COMPLETE.
    second_attempt = await repo.finalize(
        FRAME_TIME,
        [
            {
                "frame_time": FRAME_TIME,
                "instrument_id": 1,
                "open_time_1m": FRAME_TIME,
                "open_time_5m": FRAME_TIME,
                "open_time_15m": FRAME_TIME,
                "open_time_1h": FRAME_TIME,
            },
            {
                "frame_time": FRAME_TIME,
                "instrument_id": 2,
                "open_time_1m": FRAME_TIME,
                "open_time_5m": FRAME_TIME,
                "open_time_15m": FRAME_TIME,
                "open_time_1h": FRAME_TIME,
            },
        ],
        now=datetime.now(UTC),
    )

    assert second_attempt is False
    row = (await db_session.execute(select(MarketFrame))).scalars().one()
    assert row.status == FrameStatus.PARTIAL.value
    assert row.available_instruments == 1  # unchanged


async def test_finalize_deduplicates_a_repeated_member_within_one_call(db_session):
    """The composite PK (frame_time, instrument_id) plus ON CONFLICT DO
    NOTHING means even a caller bug that lists the same instrument twice
    can never produce two member rows for it."""
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))
    member = {
        "frame_time": FRAME_TIME,
        "instrument_id": 1,
        "open_time_1m": FRAME_TIME,
        "open_time_5m": FRAME_TIME,
        "open_time_15m": FRAME_TIME,
        "open_time_1h": FRAME_TIME,
    }
    await repo.finalize(FRAME_TIME, [member, member], now=datetime.now(UTC))

    members = (await db_session.execute(select(MarketFrameMember))).scalars().all()
    assert len(members) == 1


async def test_finalize_on_nonexistent_frame_does_nothing(db_session):
    repo = FrameRepository(db_session)
    finalized = await repo.finalize(FRAME_TIME, [], now=datetime.now(UTC))
    assert finalized is False
    assert (await db_session.execute(select(MarketFrame))).scalars().all() == []


# -- find_building --------------------------------------------------------------------


async def test_find_building_returns_the_building_frame(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))

    building = await repo.find_building()
    assert building is not None
    assert building.frame_time == FRAME_TIME


async def test_find_building_returns_none_once_finalized(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))
    await repo.finalize(FRAME_TIME, [], now=datetime.now(UTC))

    assert await repo.find_building() is None


# -- get_frame / get_latest_finalized with real joined candle data ------------------


async def test_get_frame_returns_full_joined_ohlcv_context(db_session, instrument_id):
    hour_start = FRAME_TIME - timedelta(hours=1)
    await _seed_full_hour_of_1m(db_session, instrument_id, hour_start)

    repo = FrameRepository(db_session)
    await repo.create_building(
        FRAME_TIME, expected_instrument_ids=[instrument_id], now=datetime.now(UTC)
    )
    members = await _member_rows_for(db_session, instrument_id, FRAME_TIME)
    assert len(members) == 1  # sanity: all 4 timeframes were available
    await repo.finalize(FRAME_TIME, members, now=datetime.now(UTC))

    frame = await repo.get_frame(FRAME_TIME)
    assert frame is not None
    assert frame.status == FrameStatus.COMPLETE
    assert frame.completeness == 1.0
    assert len(frame.members) == 1

    member = frame.members[0]
    assert member.symbol == "BTCUSDT"
    assert member.m1.open_time == FRAME_TIME - timedelta(minutes=1)
    assert member.m1.close == Decimal("59")  # close of the last (minute-59) 1m candle
    assert member.m5.open_time == FRAME_TIME - timedelta(minutes=5)
    assert member.m15.open_time == FRAME_TIME - timedelta(minutes=15)
    assert member.h1.open_time == hour_start


async def test_get_frame_returns_none_for_unknown_frame_time(db_session):
    repo = FrameRepository(db_session)
    assert await repo.get_frame(FRAME_TIME) is None


async def test_get_latest_finalized_ignores_a_still_building_frame(db_session):
    repo = FrameRepository(db_session)
    later = FRAME_TIME + timedelta(minutes=1)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))
    await repo.finalize(FRAME_TIME, [], now=datetime.now(UTC))
    await repo.create_building(later, expected_instrument_ids=[1], now=datetime.now(UTC))

    latest = await repo.get_latest_finalized()
    assert latest is not None
    assert latest.frame_time == FRAME_TIME  # not the newer, still-BUILDING one


async def test_get_latest_finalized_returns_none_when_nothing_finalized_yet(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1], now=datetime.now(UTC))

    assert await repo.get_latest_finalized() is None


# -- get_expected_instrument_ids (crash-recovery follow-up) -------------------------


async def test_get_expected_instrument_ids_returns_the_exact_snapshot(db_session):
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[3, 1, 2], now=datetime.now(UTC))

    assert await repo.get_expected_instrument_ids(FRAME_TIME) == [3, 1, 2]


async def test_get_expected_instrument_ids_returns_none_for_unknown_frame(db_session):
    repo = FrameRepository(db_session)
    assert await repo.get_expected_instrument_ids(FRAME_TIME) is None


async def test_get_expected_instrument_ids_is_unaffected_by_universe_changes(db_session):
    """The whole point of this follow-up: the snapshot never drifts,
    regardless of what happens to the active universe afterward."""
    repo = FrameRepository(db_session)
    await repo.create_building(FRAME_TIME, expected_instrument_ids=[1, 2, 3], now=datetime.now(UTC))

    # Nothing about the universe changing (elsewhere) touches this table at
    # all — but assert explicitly that re-reading gives the same snapshot
    # no matter how many times or how much "later" it's read.
    first_read = await repo.get_expected_instrument_ids(FRAME_TIME)
    second_read = await repo.get_expected_instrument_ids(FRAME_TIME)
    assert first_read == [1, 2, 3]
    assert second_read == [1, 2, 3]
