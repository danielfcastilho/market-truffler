from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.return_feature import ReturnFeature
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
CURRENT_OPEN = FRAME_TIME - timedelta(minutes=1)


def _ctx(open_time: datetime, close: str) -> FrameCandleContext:
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


def _member(instrument_id: int, symbol: str, current_close: str) -> MarketFrameMember:
    ctx = _ctx(CURRENT_OPEN, current_close)
    return MarketFrameMember(
        instrument_id=instrument_id, symbol=symbol, m1=ctx, m5=ctx, m15=ctx, h1=ctx
    )


def _frame(members: list[MarketFrameMember]) -> MarketFrame:
    return MarketFrame(
        frame_time=FRAME_TIME,
        expected_instruments=len(members),
        available_instruments=len(members),
        status=FrameStatus.COMPLETE,
        created_at=FRAME_TIME,
        finalized_at=FRAME_TIME,
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


async def _seed_1m_candle(db_session, instrument_id: int, open_time: datetime, close: str) -> None:
    repo = CandleRepository(db_session)
    await repo.upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "timeframe": "1m",
                "open_time": open_time,
                "close_time": open_time + timedelta(minutes=1) - timedelta(microseconds=1),
                "open": Decimal(close),
                "high": Decimal(close),
                "low": Decimal(close),
                "close": Decimal(close),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
        ]
    )


def _feature(timeframe: str, lookback: timedelta) -> ReturnFeature:
    return ReturnFeature(timeframe, lookback)


# -- naming / required_history ---------------------------------------------------


def test_name_and_required_history_are_derived_from_the_timeframe_label():
    feature = _feature("24h", timedelta(hours=24))
    assert feature.name == "return_24h"
    assert feature.required_history == timedelta(hours=24)


# -- formula, parametrized over the new timeframes ---------------------------------


async def test_formula_100_to_101_is_plus_one_percent_at_15m(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=15), "100")
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await _feature("15m", timedelta(minutes=15)).calculate(
        frame, CandleRepository(db_session)
    )

    assert result[iid] == Decimal("0.01")


async def test_formula_at_4h(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(hours=4), "200")
    frame = _frame([_member(iid, "BTCUSDT", "180")])

    result = await _feature("4h", timedelta(hours=4)).calculate(
        frame, CandleRepository(db_session)
    )

    assert result[iid] == Decimal("180") / Decimal("200") - 1
    assert result[iid] == Decimal("-0.1")


async def test_formula_at_24h(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(hours=24), "50")
    frame = _frame([_member(iid, "BTCUSDT", "55")])

    result = await _feature("24h", timedelta(hours=24)).calculate(
        frame, CandleRepository(db_session)
    )

    assert result[iid] == Decimal("55") / Decimal("50") - 1


async def test_missing_exact_historical_candle_is_none_never_approximated(db_session):
    iid = await _seed_instrument(db_session)
    # A candle exists, but not at exactly T-24h.
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(hours=23, minutes=59), "50")
    frame = _frame([_member(iid, "BTCUSDT", "55")])

    result = await _feature("24h", timedelta(hours=24)).calculate(
        frame, CandleRepository(db_session)
    )

    assert result[iid] is None


async def test_non_positive_historical_close_is_none():
    """A defensively-impossible but still-guarded case: never divide by a
    non-positive historical close."""
    iid = 1
    ctx_now = _ctx(CURRENT_OPEN, "100")
    member = MarketFrameMember(
        instrument_id=iid, symbol="BTCUSDT", m1=ctx_now, m5=ctx_now, m15=ctx_now, h1=ctx_now
    )
    frame = _frame([member])

    class _StubCandle:
        def __init__(self, close):
            self.close = close

    class _StubRepo:
        async def fetch_exact_open_time(self, timeframe, instrument_ids, open_time):
            return {iid: _StubCandle(Decimal("0"))}

    result = await _feature("24h", timedelta(hours=24)).calculate(frame, _StubRepo())
    assert result[iid] is None


async def test_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await _feature("24h", timedelta(hours=24)).calculate(
        frame, CandleRepository(db_session)
    )
    assert result == {}


async def test_shared_anchor_across_members_batches_into_one_query(db_session):
    """Every member sharing the same m1.open_time (the common case) must
    collapse to a single batched historical lookup, never one per
    instrument — mirrors return_5m/return_1h's own batching guarantee."""
    from unittest.mock import AsyncMock

    ids = [await _seed_instrument(db_session, f"SYM{i}USDT") for i in range(5)]
    for iid in ids:
        await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(hours=24), "100")
    frame = _frame([_member(iid, f"SYM{i}USDT", "110") for i, iid in enumerate(ids)])

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await _feature("24h", timedelta(hours=24)).calculate(frame, candle_repo)

    assert all(v == Decimal("0.1") for v in result.values())
    assert spy.await_count == 1
