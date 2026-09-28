from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.relative_volume import RelativeVolumeFeature, compute_relative_volume
from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)

_TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}
_ANCHOR_FIELD = {"15m": "m15", "1h": "h1", "4h": "h4"}


def _candle(volume: str) -> Candle:
    return Candle(
        instrument_id=0,
        timeframe="x",
        open_time=FRAME_TIME,
        close_time=FRAME_TIME,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal(volume),
        turnover=Decimal("1"),
    )


# -- compute_relative_volume: the pure formula ------------------------------------


def test_current_over_the_mean_of_the_preceding_fourteen():
    # 14 candles of volume 10 (mean baseline = 10), then a current candle
    # of volume 20 -> 20 / 10 = 2.0.
    candles = [_candle("10") for _ in range(14)] + [_candle("20")]
    assert compute_relative_volume(candles) == Decimal("2")


def test_below_normal_volume_yields_a_ratio_below_one():
    candles = [_candle("10") for _ in range(14)] + [_candle("5")]
    assert compute_relative_volume(candles) == Decimal("0.5")


def test_zero_baseline_is_none_never_an_infinite_or_fabricated_ratio():
    candles = [_candle("0") for _ in range(14)] + [_candle("5")]
    assert compute_relative_volume(candles) is None


def test_wrong_length_returns_none():
    flat = _candle("10")
    assert compute_relative_volume([flat] * 14) is None
    assert compute_relative_volume([flat] * 16) is None
    assert compute_relative_volume([]) is None


# -- RelativeVolumeFeature: DB-backed, frame-anchored -----------------------------

_FLAT_VOLUMES = ["10"] * 14 + ["20"]  # baseline 10, current 20 -> ratio 2.0


def _ctx(open_time: datetime, volume: str, duration: timedelta) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + duration - timedelta(microseconds=1),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal(volume),
        turnover=Decimal("1"),
    )


def _member(
    instrument_id: int, symbol: str, timeframe: str, anchor_open_time: datetime
) -> MarketFrameMember:
    duration = _TIMEFRAME_DURATIONS[timeframe]
    ctx = _ctx(anchor_open_time, _FLAT_VOLUMES[-1], duration)
    placeholder = _ctx(anchor_open_time, "0", timedelta(minutes=1))
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
    db_session, instrument_id: int, timeframe: str, open_time: datetime, volume: str
) -> None:
    duration = _TIMEFRAME_DURATIONS[timeframe]
    await CandleRepository(db_session).upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "open_time": open_time,
                "close_time": open_time + duration - timedelta(microseconds=1),
                "open": Decimal("100"),
                "high": Decimal("100"),
                "low": Decimal("100"),
                "close": Decimal("100"),
                "volume": Decimal(volume),
                "turnover": Decimal("1"),
            }
        ]
    )


async def _seed_window(
    db_session,
    instrument_id: int,
    timeframe: str,
    anchor_open_time: datetime,
    volumes: list[str],
) -> None:
    """Seed exactly `volumes` (oldest-first) as consecutive candles of
    `timeframe`, the last one landing exactly at `anchor_open_time`."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    start = anchor_open_time - duration * (len(volumes) - 1)
    for i, volume in enumerate(volumes):
        await _seed_candle(db_session, instrument_id, timeframe, start + duration * i, volume)


def _feature(timeframe: str) -> RelativeVolumeFeature:
    return RelativeVolumeFeature(
        timeframe,
        _TIMEFRAME_DURATIONS[timeframe],
        lambda member: getattr(member, _ANCHOR_FIELD[timeframe]),
    )


async def test_name_and_required_history_are_derived_from_the_timeframe_label():
    feature = _feature("4h")
    assert feature.name == "relative_volume_4h"
    assert feature.required_history == timedelta(hours=4) * 15


async def test_correctness_at_each_timeframe(db_session):
    for timeframe in ("15m", "1h", "4h"):
        iid = await _seed_instrument(db_session, f"SYM_{timeframe}")
        anchor = FRAME_TIME - _TIMEFRAME_DURATIONS[timeframe]
        await _seed_window(db_session, iid, timeframe, anchor, _FLAT_VOLUMES)
        frame = _frame([_member(iid, f"SYM_{timeframe}", timeframe, anchor)])

        result = await _feature(timeframe).calculate(frame, CandleRepository(db_session))

        assert result[iid] == Decimal("2")


async def test_short_history_is_none_never_shortened_or_interpolated(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, _FLAT_VOLUMES[:-1])  # only 14, not 15
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_a_gap_in_the_window_is_none_never_approximated(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    duration = timedelta(hours=1)
    start = anchor - duration * 14
    for i, volume in enumerate(_FLAT_VOLUMES):
        if i == 7:
            continue  # a hole in the middle of the window
        await _seed_candle(db_session, iid, "1h", start + duration * i, volume)
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_missing_anchor_is_none(db_session):
    """h4 not yet available for this instrument/frame (e.g. its first 4h
    bucket hasn't closed) -> relative_volume_4h must be None, never
    substituted from a different timeframe."""
    iid = await _seed_instrument(db_session)
    placeholder = _ctx(FRAME_TIME, "0", timedelta(minutes=1))
    member = MarketFrameMember(
        instrument_id=iid,
        symbol="BTCUSDT",
        m1=placeholder,
        m5=placeholder,
        m15=placeholder,
        h1=placeholder,
        h4=None,
    )
    frame = _frame([member])

    result = await _feature("4h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_a_later_candle_than_the_anchor_can_never_change_the_result(db_session):
    """No-look-ahead: a candle that closes after the frame's own anchor
    must never be selected into the window, however it's seeded."""
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, _FLAT_VOLUMES)
    # A "future" candle relative to the anchor, with a wildly different
    # volume — if this ever leaked into the window, the result would change.
    await _seed_candle(db_session, iid, "1h", anchor + timedelta(hours=1), "999999")
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("2")  # unchanged from the plain case


async def test_zero_baseline_from_a_newly_listed_instrument_is_none(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, ["0"] * 14 + ["5"])
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _feature("1h").calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await _feature("1h").calculate(frame, CandleRepository(db_session))
    assert result == {}


async def test_shared_anchor_across_members_batches_into_one_query(db_session):
    """Every member sharing the same anchor must collapse to a single
    batched historical lookup, never one per instrument — mirrors
    VolatilityFeature's own batching guarantee."""
    from unittest.mock import AsyncMock

    ids = [await _seed_instrument(db_session, f"SYM{i}USDT") for i in range(5)]
    anchor = FRAME_TIME - timedelta(hours=1)
    for iid in ids:
        await _seed_window(db_session, iid, "1h", anchor, _FLAT_VOLUMES)
    frame = _frame([_member(iid, f"SYM{i}USDT", "1h", anchor) for i, iid in enumerate(ids)])

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await _feature("1h").calculate(frame, candle_repo)

    assert all(v == Decimal("2") for v in result.values())
    assert spy.await_count == 1
