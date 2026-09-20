from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.rsi import REQUIRED_CLOSES, RsiFeature, compute_rsi_14
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)

# A hand-computable 15-close sequence: 7 alternating gains of +4 and 7
# losses of -1. avg_gain = (7*4)/14 = 2, avg_loss = (7*1)/14 = 0.5,
# RS = avg_gain/avg_loss = 4, RSI = 100 - 100/(1+4) = 80.
_HAND_VERIFIABLE_CLOSES = [
    Decimal(v)
    for v in (
        "100",
        "104",
        "103",
        "107",
        "106",
        "110",
        "109",
        "113",
        "112",
        "116",
        "115",
        "119",
        "118",
        "122",
        "121",
    )
]


# -- compute_rsi_14: the pure formula ----------------------------------------------


def test_hand_verifiable_rsi_sequence():
    assert len(_HAND_VERIFIABLE_CLOSES) == REQUIRED_CLOSES
    assert compute_rsi_14(_HAND_VERIFIABLE_CLOSES) == Decimal("80")


def test_only_gains_yields_100():
    closes = [Decimal(100 + i) for i in range(REQUIRED_CLOSES)]  # strictly increasing
    assert compute_rsi_14(closes) == Decimal("100")


def test_only_losses_yields_0():
    closes = [Decimal(100 - i) for i in range(REQUIRED_CLOSES)]  # strictly decreasing
    assert compute_rsi_14(closes) == Decimal("0")


def test_no_movement_at_all_yields_50():
    closes = [Decimal("100")] * REQUIRED_CLOSES
    assert compute_rsi_14(closes) == Decimal("50")


def test_wrong_length_returns_none():
    assert compute_rsi_14([Decimal("100")] * (REQUIRED_CLOSES - 1)) is None
    assert compute_rsi_14([Decimal("100")] * (REQUIRED_CLOSES + 1)) is None
    assert compute_rsi_14([]) is None


# -- RsiFeature: DB-backed, frame-anchored ------------------------------------------

_TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}


_ANCHOR_FIELD = {"5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4"}


def _ctx(open_time: datetime, close: str, duration: timedelta) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + duration - timedelta(microseconds=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


def _member(
    instrument_id: int, symbol: str, timeframe: str, anchor_open_time: datetime
) -> MarketFrameMember:
    """A member whose *only* populated timeframe field is the one under
    test — RsiFeature never looks at the others, so a placeholder m1
    context is enough to satisfy the dataclass."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    ctx = _ctx(anchor_open_time, "0", duration)
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
    db_session, instrument_id: int, timeframe: str, open_time: datetime, close: Decimal
) -> None:
    duration = _TIMEFRAME_DURATIONS[timeframe]
    repo = CandleRepository(db_session)
    await repo.upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "open_time": open_time,
                "close_time": open_time + duration - timedelta(microseconds=1),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
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
    closes: list[Decimal],
) -> None:
    """Seed exactly `closes` (oldest-first) as consecutive candles of
    `timeframe`, the last one landing exactly at `anchor_open_time`."""
    duration = _TIMEFRAME_DURATIONS[timeframe]
    start = anchor_open_time - duration * (len(closes) - 1)
    for i, close in enumerate(closes):
        await _seed_candle(db_session, instrument_id, timeframe, start + duration * i, close)


async def _run(timeframe: str, frame: MarketFrame, db_session) -> dict[int, Decimal | None]:
    feature = RsiFeature(
        timeframe,
        _TIMEFRAME_DURATIONS[timeframe],
        lambda member: getattr(member, _ANCHOR_FIELD[timeframe]),
    )
    return await feature.calculate(frame, CandleRepository(db_session))


async def test_rsi_14_5m_correctness(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(minutes=5)
    await _seed_window(db_session, iid, "5m", anchor, _HAND_VERIFIABLE_CLOSES)
    frame = _frame([_member(iid, "BTCUSDT", "5m", anchor)])

    result = await _run("5m", frame, db_session)

    assert result[iid] == Decimal("80")


async def test_rsi_14_15m_correctness(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(minutes=15)
    await _seed_window(db_session, iid, "15m", anchor, _HAND_VERIFIABLE_CLOSES)
    frame = _frame([_member(iid, "BTCUSDT", "15m", anchor)])

    result = await _run("15m", frame, db_session)

    assert result[iid] == Decimal("80")


async def test_rsi_14_1h_correctness(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_window(db_session, iid, "1h", anchor, _HAND_VERIFIABLE_CLOSES)
    frame = _frame([_member(iid, "BTCUSDT", "1h", anchor)])

    result = await _run("1h", frame, db_session)

    assert result[iid] == Decimal("80")


async def test_rsi_14_4h_correctness(db_session):
    iid = await _seed_instrument(db_session)
    anchor = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)  # UTC-aligned 4h boundary
    await _seed_window(db_session, iid, "4h", anchor, _HAND_VERIFIABLE_CLOSES)
    frame = _frame([_member(iid, "BTCUSDT", "4h", anchor)])

    result = await _run("4h", frame, db_session)

    assert result[iid] == Decimal("80")


async def test_insufficient_history_returns_none(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(minutes=5)
    # Only 10 of the required 15 closes.
    await _seed_window(db_session, iid, "5m", anchor, _HAND_VERIFIABLE_CLOSES[-10:])
    frame = _frame([_member(iid, "BTCUSDT", "5m", anchor)])

    result = await _run("5m", frame, db_session)

    assert result[iid] is None


async def test_missing_candle_in_the_middle_returns_none_not_nearest(db_session):
    """A gap must never be bridged by the nearest available candle on
    either side — even with 15 total rows present, a hole in the required
    exact window makes the feature unavailable."""
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(minutes=5)
    duration = timedelta(minutes=5)
    start = anchor - duration * (REQUIRED_CLOSES - 1)
    for i, close in enumerate(_HAND_VERIFIABLE_CLOSES):
        if i == 7:  # drop one candle in the middle of the window
            continue
        await _seed_candle(db_session, iid, "5m", start + duration * i, close)
    # Backfill the count to 15 total rows, but *outside* the required
    # window, so a naive "do we have >= 15 rows" check would wrongly pass.
    await _seed_candle(db_session, iid, "5m", start - duration, Decimal("999"))
    frame = _frame([_member(iid, "BTCUSDT", "5m", anchor)])

    result = await _run("5m", frame, db_session)

    assert result[iid] is None


async def test_future_candles_cannot_affect_older_frame_rsi(db_session):
    iid = await _seed_instrument(db_session)
    anchor = FRAME_TIME - timedelta(minutes=5)
    await _seed_window(db_session, iid, "5m", anchor, _HAND_VERIFIABLE_CLOSES)
    # Candles arriving after the frame's own anchor must never leak in.
    await _seed_candle(db_session, iid, "5m", anchor + timedelta(minutes=5), Decimal("999999"))
    await _seed_candle(db_session, iid, "5m", anchor + timedelta(minutes=10), Decimal("1"))
    frame = _frame([_member(iid, "BTCUSDT", "5m", anchor)])

    result = await _run("5m", frame, db_session)

    assert result[iid] == Decimal("80")  # unchanged from the no-future-data case


async def test_missing_anchor_yields_none_without_querying():
    """When the frame itself has no h4 for an instrument (e.g. its first 4h
    bucket hasn't closed yet — see MarketFrameMember.h4), rsi_14_4h must be
    None without needing any candle to exist at all."""
    placeholder = _ctx(FRAME_TIME, "0", timedelta(minutes=1))
    member = MarketFrameMember(
        instrument_id=1,
        symbol="BTCUSDT",
        m1=placeholder,
        m5=placeholder,
        m15=placeholder,
        h1=placeholder,
    )
    frame = _frame([member])

    feature = RsiFeature("4h", timedelta(hours=4), lambda member: member.h4)
    result = await feature.calculate(frame, None)  # type: ignore[arg-type]

    assert result[1] is None


async def test_empty_frame_returns_empty_result(db_session):
    feature = RsiFeature("5m", timedelta(minutes=5), lambda member: member.m5)
    result = await feature.calculate(_frame([]), CandleRepository(db_session))
    assert result == {}


# -- batching -----------------------------------------------------------------------


async def test_rsi_history_lookup_is_batched_not_per_instrument(db_session):
    """M5 section 12, extended to RSI: resolving the 15-close window for
    many instruments sharing the same anchor must be one query, not N."""
    from unittest.mock import AsyncMock

    anchor = FRAME_TIME - timedelta(minutes=5)
    ids = []
    for i in range(25):
        iid = await _seed_instrument(db_session, f"SYM{i}USDT")
        await _seed_window(db_session, iid, "5m", anchor, _HAND_VERIFIABLE_CLOSES)
        ids.append(iid)
    frame = _frame([_member(iid, f"SYM{i}USDT", "5m", anchor) for i, iid in enumerate(ids)])

    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await _run("5m", frame, db_session)

    assert len(result) == 25
    assert all(v == Decimal("80") for v in result.values())
    assert spy.await_count == 1  # one shared anchor time -> one batched query


async def test_full_feature_engine_query_count_does_not_scale_with_instrument_count(db_session):
    """Running every configured feature (return_5m/return_1h + all four
    RSI timeframes) over many instruments sharing the same frame-relative
    anchors must issue a small, bounded number of queries — never one per
    instrument per feature."""
    from unittest.mock import AsyncMock

    from app.features.engine import FEATURES

    anchor_1m = FRAME_TIME - timedelta(minutes=1)
    anchors = {
        "5m": FRAME_TIME - timedelta(minutes=5),
        "15m": FRAME_TIME - timedelta(minutes=15),
        "1h": FRAME_TIME - timedelta(hours=1),
        "4h": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    }

    members = []
    for i in range(20):
        iid = await _seed_instrument(db_session, f"SYM{i}USDT")
        for timeframe, anchor in anchors.items():
            await _seed_window(db_session, iid, timeframe, anchor, _HAND_VERIFIABLE_CLOSES)
        ctx1m = _ctx(anchor_1m, "100", timedelta(minutes=1))
        members.append(
            MarketFrameMember(
                instrument_id=iid,
                symbol=f"SYM{i}USDT",
                m1=ctx1m,
                m5=_ctx(anchors["5m"], "0", _TIMEFRAME_DURATIONS["5m"]),
                m15=_ctx(anchors["15m"], "0", _TIMEFRAME_DURATIONS["15m"]),
                h1=_ctx(anchors["1h"], "0", _TIMEFRAME_DURATIONS["1h"]),
                h4=_ctx(anchors["4h"], "0", _TIMEFRAME_DURATIONS["4h"]),
            )
        )
    frame = _frame(members)

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    per_feature = {
        feature.name: await feature.calculate(frame, candle_repo) for feature in FEATURES
    }

    assert all(len(values) == 20 for values in per_feature.values())
    assert all(v == Decimal("80") for v in per_feature["rsi_14_5m"].values())
    # 6 features, each with one shared anchor across all 20 instruments ->
    # 6 queries total, not 6 * 20 = 120.
    assert spy.await_count == len(FEATURES)
