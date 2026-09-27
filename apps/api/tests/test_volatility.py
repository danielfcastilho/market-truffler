from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.volatility import VolatilityFeature, compute_atr_14
from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)

_TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}
_ANCHOR_FIELD = {"15m": "m15", "1h": "h1", "4h": "h4", "24h": "h24"}


def _candle(open_: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        instrument_id=0,
        timeframe="x",
        open_time=FRAME_TIME,
        close_time=FRAME_TIME,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


# -- compute_atr_14: the pure formula ----------------------------------------------


def test_flat_symmetric_range_is_hand_verifiable():
    """15 candles, each with high=100.5/low=99.5/close=100 (and the same
    close preceding each) -> every one of the 14 True Ranges is exactly
    the intrabar range, 1: max(1, |100.5-100|, |99.5-100|) = max(1, 0.5,
    0.5) = 1. ATR(14) = mean(1 for 14) = 1."""
    candles = [_candle("100", "100.5", "99.5", "100") for _ in range(15)]
    assert compute_atr_14(candles) == Decimal("1")


def test_a_gap_between_candles_dominates_the_intrabar_range():
    """14 flat candles (TR=1 each, 13 pairs among them) followed by one
    that gaps sharply up from the prior close of 100 to a 108-110 range
    -> TR_14 = max(high-low=2, |110-100|=10, |108-100|=8) = 10. Total =
    13*1 + 10 = 23, over 14 -> 23/14, never just the final candle's own
    intrabar range (2)."""
    candles = [_candle("100", "100.5", "99.5", "100") for _ in range(14)]
    candles.append(_candle("109", "110", "108", "109"))
    assert compute_atr_14(candles) == Decimal(23) / Decimal(14)


def test_wrong_length_returns_none():
    flat = _candle("100", "100.5", "99.5", "100")
    assert compute_atr_14([flat] * 14) is None
    assert compute_atr_14([flat] * 16) is None
    assert compute_atr_14([]) is None


# -- VolatilityFeature: DB-backed, frame-anchored -----------------------------------

_FLAT_CANDLES = [("100", "100.5", "99.5", "100")] * 15


def _ctx(
    open_time: datetime, ohlc: tuple[str, str, str, str], duration: timedelta
) -> FrameCandleContext:
    o, h, low, c = ohlc
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + duration - timedelta(microseconds=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


def _member(
    instrument_id: int, symbol: str, timeframe: str, anchor_open_time: datetime
) -> MarketFrameMember:
    """A member whose *only* populated timeframe field is the one under
    test — VolatilityFeature never looks at the others, so a placeholder
    m1 context is enough to satisfy the dataclass."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    ctx = _ctx(anchor_open_time, _FLAT_CANDLES[-1], duration)
    placeholder = _ctx(anchor_open_time, ("0", "0", "0", "0"), timedelta(minutes=1))
    fields = {"m1": placeholder, "m5": placeholder, "m15": placeholder, "h1": placeholder}
    fields[_ANCHOR_FIELD[timeframe]] = ctx
    return MarketFrameMember(instrument_id=instrument_id, symbol=symbol, **fields)


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


async def _seed_candle(
    db_session,
    instrument_id: int,
    timeframe: str,
    open_time: datetime,
    ohlc: tuple[str, str, str, str],
) -> None:
    duration = _TIMEFRAME_DURATIONS[timeframe]
    o, h, low, c = ohlc
    repo = CandleRepository(db_session)
    await repo.upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "open_time": open_time,
                "close_time": open_time + duration - timedelta(microseconds=1),
                "open": Decimal(o),
                "high": Decimal(h),
                "low": Decimal(low),
                "close": Decimal(c),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
        ]
    )


async def _seed_window(
    db_session,
    instrument_id: int,
    timeframe: str,
    anchor_open_time: datetime,
    candles: list[tuple[str, str, str, str]],
) -> None:
    """Seed exactly `candles` (oldest-first) as consecutive candles of
    `timeframe`, the last one landing exactly at `anchor_open_time`."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    start = anchor_open_time - duration * (len(candles) - 1)
    for i, ohlc in enumerate(candles):
        await _seed_candle(db_session, instrument_id, timeframe, start + duration * i, ohlc)


def _feature(timeframe: str) -> VolatilityFeature:
    return VolatilityFeature(
        timeframe,
        _TIMEFRAME_DURATIONS[timeframe],
        lambda member: getattr(member, _ANCHOR_FIELD[timeframe]),
    )


async def test_name_and_required_history_are_derived_from_the_timeframe_label():
    feature = _feature("24h")
    assert feature.name == "volatility_24h"
    assert feature.required_history == timedelta(hours=24) * 15


async def test_correctness_at_each_timeframe(db_session):
    for timeframe in ("15m", "1h", "4h", "24h"):
        iid = await _seed_instrument(db_session, f"SYM_{timeframe}")
        anchor = FRAME_TIME - _TIMEFRAME_DURATIONS[timeframe]
        await _seed_window(db_session, iid, timeframe, anchor, _FLAT_CANDLES)
        frame = _frame([_member(iid, f"SYM_{timeframe}", timeframe, anchor)])

        result = await _feature(timeframe).calculate(frame, CandleRepository(db_session))

        # ATR=1, current_price=100 (the anchor candle's close) -> 0.01.
        assert result[iid] == Decimal("0.01")


async def test_short_history_is_none_never_shortened_or_interpolated(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, _FLAT_CANDLES[:-1])  # only 14, not 15
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_a_gap_in_the_window_is_none_never_approximated(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    duration = timedelta(hours=1)
    start = anchor - duration * 14
    for i, ohlc in enumerate(_FLAT_CANDLES):
        if i == 7:
            continue  # a hole in the middle of the window
        await _seed_candle(db_session, iid, "1h", start + duration * i, ohlc)
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_missing_anchor_is_none(db_session):
    """h24 not yet available for this instrument/frame (e.g. its first
    24h bucket hasn't closed) -> volatility_24h must be None, never
    substituted from a different timeframe."""
    iid = await _seed_instrument(db_session)
    placeholder = _ctx(FRAME_TIME, ("0", "0", "0", "0"), timedelta(minutes=1))
    member = MarketFrameMember(
        instrument_id=iid,
        symbol="BTCUSDT",
        m1=placeholder,
        m5=placeholder,
        m15=placeholder,
        h1=placeholder,
        h4=None,
        h24=None,
    )
    frame = _frame([member])

    result = await _feature("24h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_a_later_candle_than_the_anchor_can_never_change_the_result(db_session):
    """No-look-ahead: a candle that closes after the frame's own anchor
    must never be selected into the window, however it's seeded."""
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, _FLAT_CANDLES)
    # A "future" candle relative to the anchor, with a wildly different
    # shape — if this ever leaked into the window, the result would change.
    future_ohlc = ("500", "600", "400", "500")
    await _seed_candle(db_session, iid, "1h", anchor + timedelta(hours=1), future_ohlc)
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("0.01")  # unchanged from the plain flat-window case


async def test_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await _feature("1h").calculate(frame, CandleRepository(db_session))
    assert result == {}


async def test_shared_anchor_across_members_batches_into_one_query(db_session):
    """Every member sharing the same anchor must collapse to a single
    batched historical lookup, never one per instrument — mirrors
    RsiFeature's own batching guarantee."""
    from unittest.mock import AsyncMock

    ids = [await _seed_instrument(db_session, f"SYM{i}USDT") for i in range(5)]
    anchor = FRAME_TIME - timedelta(hours=1)
    for iid in ids:
        await _seed_window(db_session, iid, "1h", anchor, _FLAT_CANDLES)
    frame = _frame([_member(iid, f"SYM{i}USDT", "1h", anchor) for i, iid in enumerate(ids)])

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await _feature("1h").calculate(frame, candle_repo)

    assert all(v == Decimal("0.01") for v in result.values())
    assert spy.await_count == 1
