from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.engine import FeatureEngine
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
CURRENT_OPEN = FRAME_TIME - timedelta(minutes=1)
ANALYZED_AT = datetime(2026, 1, 1, 14, 0, 6, tzinfo=UTC)


def _ctx(close: str) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=CURRENT_OPEN,
        close_time=CURRENT_OPEN + timedelta(minutes=1) - timedelta(microseconds=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


async def _seed_instrument(db_session, symbol: str) -> int:
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


async def test_engine_assembles_named_feature_results_per_instrument(db_session):
    btc = await _seed_instrument(db_session, "BTCUSDT")
    candle_repo = CandleRepository(db_session)
    await candle_repo.upsert_many(
        [
            {
                "instrument_id": btc,
                "timeframe": "1m",
                "open_time": CURRENT_OPEN - timedelta(minutes=5),
                "close_time": CURRENT_OPEN - timedelta(minutes=4) - timedelta(microseconds=1),
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": Decimal("100"),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
        ]
    )
    ctx = _ctx("101")
    frame = MarketFrame(
        frame_time=FRAME_TIME,
        expected_instruments=1,
        available_instruments=1,
        status=FrameStatus.COMPLETE,
        created_at=FRAME_TIME,
        finalized_at=FRAME_TIME,
        members=[
            MarketFrameMember(instrument_id=btc, symbol="BTCUSDT", m1=ctx, m5=ctx, m15=ctx, h1=ctx)
        ],
    )

    result = await FeatureEngine().run(frame, candle_repo, analyzed_at=ANALYZED_AT)

    assert result.frame_time == FRAME_TIME
    assert result.analyzed_at == ANALYZED_AT
    assert len(result.instruments) == 1
    instrument = result.instruments[0]
    assert instrument.instrument_id == btc
    assert instrument.symbol == "BTCUSDT"
    assert set(instrument.features) == {
        "return_5m",
        "return_1h",
        "rsi_14_5m",
        "rsi_14_15m",
        "rsi_14_1h",
        "rsi_14_4h",
    }
    assert instrument.features["return_1h"] is None
    assert instrument.features["return_5m"] == Decimal("0.01")
    # No 15-candle history was seeded for any RSI timeframe — all unavailable.
    assert instrument.features["rsi_14_5m"] is None
    assert instrument.features["rsi_14_15m"] is None
    assert instrument.features["rsi_14_1h"] is None
    assert instrument.features["rsi_14_4h"] is None


async def test_engine_only_analyzes_actual_frame_members(db_session):
    """An instrument that is NOT a frame member (e.g. excluded from a
    PARTIAL frame) must never appear in the engine's output, however much
    candle data exists for it."""
    member_id = await _seed_instrument(db_session, "BTCUSDT")
    non_member_id = await _seed_instrument(db_session, "ETHUSDT")
    candle_repo = CandleRepository(db_session)
    # ETH has plenty of real data — but it's simply not a member of this frame.
    await candle_repo.upsert_many(
        [
            {
                "instrument_id": non_member_id,
                "timeframe": "1m",
                "open_time": CURRENT_OPEN,
                "close_time": CURRENT_OPEN + timedelta(minutes=1) - timedelta(microseconds=1),
                "open": Decimal("1"),
                "high": Decimal("1"),
                "low": Decimal("1"),
                "close": Decimal("1"),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
        ]
    )
    ctx = _ctx("101")
    frame = MarketFrame(
        frame_time=FRAME_TIME,
        expected_instruments=2,
        available_instruments=1,
        status=FrameStatus.PARTIAL,
        created_at=FRAME_TIME,
        finalized_at=FRAME_TIME,
        members=[
            MarketFrameMember(
                instrument_id=member_id, symbol="BTCUSDT", m1=ctx, m5=ctx, m15=ctx, h1=ctx
            )
        ],
    )

    result = await FeatureEngine().run(frame, candle_repo, analyzed_at=ANALYZED_AT)

    assert {i.instrument_id for i in result.instruments} == {member_id}


async def test_engine_with_no_members_produces_no_instrument_results(db_session):
    frame = MarketFrame(
        frame_time=FRAME_TIME,
        expected_instruments=0,
        available_instruments=0,
        status=FrameStatus.COMPLETE,
        created_at=FRAME_TIME,
        finalized_at=FRAME_TIME,
        members=[],
    )
    result = await FeatureEngine().run(frame, CandleRepository(db_session), analyzed_at=ANALYZED_AT)
    assert result.instruments == []
