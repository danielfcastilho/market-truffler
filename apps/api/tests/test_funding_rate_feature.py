from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.funding_rate import FundingRate24hAvgFeature, FundingRateCurrentFeature
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.funding_rate_repository import FundingRateRepository

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


async def _seed_funding(db_session, instrument_id: int, funding_time: datetime, value: str) -> None:
    await FundingRateRepository(db_session).upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "funding_time": funding_time,
                "funding_rate": Decimal(value),
            }
        ]
    )


# -- naming / required_history ---------------------------------------------------


def test_current_feature_name_and_required_history():
    feature = FundingRateCurrentFeature()
    assert feature.name == "funding_rate_current"
    assert feature.required_history == timedelta(hours=24)


def test_24h_avg_feature_name_and_required_history():
    feature = FundingRate24hAvgFeature()
    assert feature.name == "funding_rate_24h_avg"
    assert feature.required_history == timedelta(hours=24)


# -- funding_rate_current -------------------------------------------------------


async def test_current_uses_the_latest_observation_at_or_before_frame_time_no_lookahead(
    db_session,
):
    iid = await _seed_instrument(db_session)
    await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=8), "0.0001")
    # A "future" observation after frame_time must never be used as "current".
    await _seed_funding(db_session, iid, FRAME_TIME + timedelta(hours=8), "0.9999")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRateCurrentFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] == Decimal("0.0001")


async def test_current_returns_a_zero_rate_as_a_legitimate_value_not_unavailable(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=8), "0")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRateCurrentFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] == Decimal("0")


async def test_current_returns_a_negative_rate_as_a_legitimate_value(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=8), "-0.0025")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRateCurrentFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] == Decimal("-0.0025")


async def test_current_with_no_observation_at_all_is_none(db_session):
    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRateCurrentFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] is None


async def test_current_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await FundingRateCurrentFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )
    assert result == {}


async def test_current_missing_funding_repo_raises_rather_than_silently_returning_none(db_session):
    import pytest

    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    with pytest.raises(ValueError):
        await FundingRateCurrentFeature().calculate(frame, CandleRepository(db_session))


# -- funding_rate_24h_avg --------------------------------------------------------


async def test_avg_is_the_plain_mean_of_every_observation_in_the_trailing_24h(db_session):
    iid = await _seed_instrument(db_session)
    for hours_ago, rate in [(0, "0.0003"), (8, "0.0001"), (16, "-0.0001")]:
        await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=hours_ago), rate)
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRate24hAvgFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] == (Decimal("0.0003") + Decimal("0.0001") + Decimal("-0.0001")) / 3


async def test_avg_excludes_observations_older_than_24h_and_never_looks_ahead(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=8), "0.0002")
    # Outside the trailing 24h window — must not affect the average.
    await _seed_funding(db_session, iid, FRAME_TIME - timedelta(hours=25), "0.9")
    # After frame_time — must never be used (no-look-ahead).
    await _seed_funding(db_session, iid, FRAME_TIME + timedelta(hours=1), "0.9")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRate24hAvgFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] == Decimal("0.0002")


async def test_avg_a_denser_funding_interval_naturally_averages_more_points(db_session):
    """A 1h-interval instrument and an 8h-interval one over the same 24h
    window each average however many real settlements they actually had
    — never normalized to a fixed count."""
    hourly_id = await _seed_instrument(db_session, "HOURLYUSDT")
    for h in range(24):
        await _seed_funding(db_session, hourly_id, FRAME_TIME - timedelta(hours=h), "0.0001")
    eight_hourly_id = await _seed_instrument(db_session, "EIGHTHUSDT")
    for h in (0, 8, 16):
        await _seed_funding(db_session, eight_hourly_id, FRAME_TIME - timedelta(hours=h), "0.0001")
    frame = _frame([_member(hourly_id, "HOURLYUSDT"), _member(eight_hourly_id, "EIGHTHUSDT")])

    result = await FundingRate24hAvgFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    # Same uniform rate either way -> same average value, but arrived at
    # from a different number of underlying observations per instrument.
    assert result[hourly_id] == Decimal("0.0001")
    assert result[eight_hourly_id] == Decimal("0.0001")


async def test_avg_with_no_observations_in_the_window_is_none(db_session):
    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await FundingRate24hAvgFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )

    assert result[iid] is None


async def test_avg_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await FundingRate24hAvgFeature().calculate(
        frame, CandleRepository(db_session), funding_repo=FundingRateRepository(db_session)
    )
    assert result == {}


async def test_avg_missing_funding_repo_raises_rather_than_silently_returning_none(db_session):
    import pytest

    iid = await _seed_instrument(db_session)
    frame = _frame([_member(iid, "BTCUSDT")])

    with pytest.raises(ValueError):
        await FundingRate24hAvgFeature().calculate(frame, CandleRepository(db_session))
