from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.open_interest_change import OpenInterestChangeFeature
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.open_interest_repository import OpenInterestRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
CURRENT_OPEN = FRAME_TIME - timedelta(minutes=1)


def _ctx(open_time: datetime, close: str = "1") -> FrameCandleContext:
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(microseconds=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


def _member(instrument_id: int, symbol: str) -> MarketFrameMember:
    ctx = _ctx(CURRENT_OPEN)
    return MarketFrameMember(
        instrument_id=instrument_id, symbol=symbol, m1=ctx, m5=ctx, m15=ctx, h1=ctx
    )


def _frame(members: list[MarketFrameMember], frame_time: datetime = FRAME_TIME) -> MarketFrame:
    return MarketFrame(
        frame_time=frame_time,
        expected_instruments=len(members),
        available_instruments=len(members),
        status=FrameStatus.COMPLETE,
        created_at=frame_time,
        finalized_at=frame_time,
        members=members,
    )


async def _seed_instrument(db_session, symbol: str = "BTCUSDT") -> int:
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
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


async def _seed_oi(db_session, instrument_id: int, observed_at: datetime, value: str) -> None:
    await OpenInterestRepository(db_session).upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "observed_at": observed_at,
                "open_interest": Decimal(value),
            }
        ]
    )


def _feature(timeframe: str, lookback: timedelta) -> OpenInterestChangeFeature:
    return OpenInterestChangeFeature(timeframe, lookback)


# -- naming / required_history ---------------------------------------------------


def test_name_and_required_history_are_derived_from_the_timeframe_label():
    feature = _feature("1h", timedelta(hours=1))
    assert feature.name == "oi_change_1h"
    assert feature.required_history == timedelta(hours=1)


# -- formula ------------------------------------------------------------------


async def test_formula_is_current_over_previous_minus_one(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_oi(db_session, iid, FRAME_TIME, "1000")
    await _seed_oi(db_session, iid, FRAME_TIME - timedelta(hours=1), "900")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )

    assert result[iid] == Decimal("1000") / Decimal("900") - 1


async def test_stored_as_a_raw_fraction_never_pre_multiplied_by_100(db_session):
    """+5% is stored as 0.05, exactly like return_5m/return_1h — never 5,
    so the frontend's existing Return formatting can be reused unchanged."""
    iid = await _seed_instrument(db_session)
    await _seed_oi(db_session, iid, FRAME_TIME, "105")
    await _seed_oi(db_session, iid, FRAME_TIME - timedelta(hours=1), "100")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )

    assert result[iid] == Decimal("0.05")


async def test_current_oi_uses_the_latest_observation_at_or_before_frame_time_no_lookahead(
    db_session,
):
    iid = await _seed_instrument(db_session)
    # A "future" observation after frame_time must never be used as "current".
    await _seed_oi(db_session, iid, FRAME_TIME - timedelta(minutes=5), "1000")
    await _seed_oi(db_session, iid, FRAME_TIME + timedelta(minutes=5), "999999")
    await _seed_oi(db_session, iid, FRAME_TIME - timedelta(minutes=5) - timedelta(hours=1), "900")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )

    assert result[iid] == Decimal("1000") / Decimal("900") - 1


async def test_missing_current_observation_is_none(db_session):
    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )

    assert result[iid] is None


async def test_missing_previous_observation_is_none_never_approximated(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_oi(db_session, iid, FRAME_TIME, "1000")
    # No observation exactly 1h before the current anchor.
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )

    assert result[iid] is None


async def test_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), OpenInterestRepository(db_session)
    )
    assert result == {}


async def test_missing_oi_repo_raises_rather_than_silently_returning_none(db_session):
    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    import pytest

    with pytest.raises(ValueError):
        await _feature("1h", timedelta(hours=1)).calculate(frame, CandleRepository(db_session))


async def test_shared_anchor_across_members_batches_into_two_queries(db_session):
    """Every member sharing the same current-OI anchor must collapse to
    one "current" lookup plus one "previous" lookup, never one pair per
    instrument."""
    from unittest.mock import AsyncMock

    ids = [await _seed_instrument(db_session, f"SYM{i}USDT") for i in range(5)]
    for iid in ids:
        await _seed_oi(db_session, iid, FRAME_TIME, "110")
        await _seed_oi(db_session, iid, FRAME_TIME - timedelta(hours=1), "100")
    frame = _frame([_member(iid, f"SYM{i}USDT") for i, iid in enumerate(ids)])

    oi_repo = OpenInterestRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await _feature("1h", timedelta(hours=1)).calculate(
        frame, CandleRepository(db_session), oi_repo
    )

    assert all(v == Decimal("0.1") for v in result.values())
    assert spy.await_count == 2
