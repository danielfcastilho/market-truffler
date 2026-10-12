from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.repositories.candle_repository import CandleRepository
from app.research.forward_returns import (
    compute_forward_relative_returns,
    compute_forward_returns,
    validate_horizons,
)

# The canonical example: the 5m candle opened at 10:00 closes at 10:05, so
# a decision at frame_time = 10:05 has it as `member.m5` (its close is
# P(T)). A 15m forward return from 10:05 runs to 10:20: P(T+H) is the
# close of the candle opened at 10:15 (closing at 10:20).
FRAME_TIME = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
FIVE = timedelta(minutes=5)
HORIZONS = {"15m": timedelta(minutes=15)}


def _at(hh: int, mm: int) -> datetime:
    return datetime(2026, 1, 1, hh, mm, tzinfo=UTC)


def _ctx(open_time: datetime, close: str) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + FIVE - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("1"),
    )


def _member(
    instrument_id: int,
    symbol: str,
    start_close: str = "100",
    m5_open_time: datetime = FRAME_TIME - FIVE,
) -> MarketFrameMember:
    m5 = _ctx(m5_open_time, start_close)
    return MarketFrameMember(instrument_id=instrument_id, symbol=symbol, m5=m5, m15=m5, h1=m5)


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


async def _seed_5m(db_session, instrument_id: int, open_time: datetime, close: str) -> None:
    await CandleRepository(db_session).upsert_many(
        [
            {
                "instrument_id": instrument_id,
                "timeframe": "5m",
                "open_time": open_time,
                "close_time": open_time + FIVE - timedelta(milliseconds=1),
                "open": Decimal(close),
                "high": Decimal(close),
                "low": Decimal(close),
                "close": Decimal(close),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
        ]
    )


async def _seed_instrument(db_session, symbol: str = "BTCUSDT") -> int:
    from app.models.instrument import Instrument

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


# -- compute_forward_returns: exact temporal semantics ----------------------------


async def test_forward_return_from_1005_to_1020_uses_the_candles_closing_at_each_end(db_session):
    """T = 10:05, H = 15m. P(T) is the 10:00 candle (closes 10:05), P(T+H)
    the 10:15 candle (closes 10:20). Decoys: the 10:20 candle — what a
    naive `frame_time + horizon` target would pick, but it doesn't close
    until 10:25 — and the 10:10 candle, one candle short. Either being
    picked shows up as an obviously wrong value."""
    iid = await _seed_instrument(db_session)
    await _seed_5m(db_session, iid, _at(10, 10), "555")  # one candle short — decoy
    await _seed_5m(db_session, iid, _at(10, 15), "110")  # closes 10:20 — correct
    await _seed_5m(db_session, iid, _at(10, 20), "999")  # opens at T+H — decoy
    frame = _frame([_member(iid, "BTCUSDT", start_close="100", m5_open_time=_at(10, 0))])

    result = await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)

    assert result[iid]["forward_return_15m"] == Decimal("0.1")  # 110/100 - 1, never a decoy


async def test_start_price_is_the_frames_own_m5_close_never_a_fresh_query(db_session):
    """P(T) is read straight from `member.m5.close` — even if a different
    10:00 row sits in storage, and even though the 10:05 candle (still
    forming at 10:05) might exist by now, neither is ever used."""
    iid = await _seed_instrument(db_session)
    await _seed_5m(db_session, iid, _at(10, 0), "7777")  # storage disagrees with the frame
    await _seed_5m(db_session, iid, _at(10, 5), "8888")  # closes 10:10 — not knowable at 10:05
    await _seed_5m(db_session, iid, _at(10, 15), "150")
    frame = _frame([_member(iid, "BTCUSDT", start_close="50")])

    result = await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)

    assert result[iid]["forward_return_15m"] == Decimal("2")  # 150/50 - 1


async def test_stale_m5_anchor_yields_none_rather_than_a_stretched_label(db_session):
    """A member whose latest legal 5m candle is older than [T-5m, T) (a
    gap right before the frame) has no exact P(T): using its stale close
    would silently measure over more than H, so every horizon is None."""
    iid = await _seed_instrument(db_session)
    await _seed_5m(db_session, iid, _at(10, 15), "110")
    frame = _frame([_member(iid, "BTCUSDT", m5_open_time=_at(9, 55))])

    result = await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)

    assert result[iid]["forward_return_15m"] is None


async def test_missing_exact_future_candle_is_none_never_approximated(db_session):
    iid = await _seed_instrument(db_session)
    # Candles exist either side, but not at the exact 10:15 target.
    await _seed_5m(db_session, iid, _at(10, 10), "110")
    await _seed_5m(db_session, iid, _at(10, 20), "110")
    frame = _frame([_member(iid, "BTCUSDT")])

    result = await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)

    assert result[iid]["forward_return_15m"] is None


async def test_multiple_horizons_are_each_computed_independently(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_5m(db_session, iid, _at(10, 15), "110")  # 15m target, closes 10:20
    await _seed_5m(db_session, iid, _at(11, 0), "200")  # 1h target, closes 11:05
    frame = _frame([_member(iid, "BTCUSDT", start_close="100")])
    horizons = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1)}

    result = await compute_forward_returns(frame, CandleRepository(db_session), horizons)

    assert result[iid]["forward_return_15m"] == Decimal("0.1")
    assert result[iid]["forward_return_1h"] == Decimal("1")


@pytest.mark.parametrize(
    "horizon", [timedelta(minutes=7), timedelta(minutes=1), timedelta(0), timedelta(minutes=-5)]
)
async def test_horizons_not_a_whole_number_of_5m_candles_are_rejected(db_session, horizon):
    """No candle closes exactly at T+7m — rejected outright, never rounded
    to 5m or 10m."""
    frame = _frame([_member(1, "BTCUSDT")])
    with pytest.raises(ValueError, match="5m"):
        await compute_forward_returns(frame, CandleRepository(db_session), {"x": horizon})


def test_validate_horizons_accepts_multiples_of_5m():
    validate_horizons(
        {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "24h": timedelta(hours=24)}
    )


async def test_frame_time_off_a_5m_boundary_is_rejected(db_session):
    frame = _frame([_member(1, "BTCUSDT")], frame_time=_at(10, 7))
    with pytest.raises(ValueError, match="boundary"):
        await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)


async def test_no_members_returns_empty_dict(db_session):
    frame = _frame([])
    result = await compute_forward_returns(frame, CandleRepository(db_session), HORIZONS)
    assert result == {}


async def test_every_member_shares_one_batched_query_per_horizon(db_session):
    """All members of one frame share the same frame_time, so the target
    open_time per horizon is identical across all of them — this must
    collapse to one query per horizon, never one per instrument."""
    ids = [await _seed_instrument(db_session, f"SYM{i}USDT") for i in range(5)]
    for iid in ids:
        await _seed_5m(db_session, iid, _at(10, 15), "110")
    frame = _frame([_member(iid, f"SYM{i}USDT") for i, iid in enumerate(ids)])

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await compute_forward_returns(frame, candle_repo, HORIZONS)

    assert all(v["forward_return_15m"] == Decimal("0.1") for v in result.values())
    assert spy.await_count == 1  # one horizon -> one query


# -- compute_forward_relative_returns: cross-sectional median --------------------


def test_relative_return_is_the_instruments_value_minus_the_median():
    forward = {
        1: {"forward_return_15m": Decimal("0.10")},
        2: {"forward_return_15m": Decimal("0.20")},
        3: {"forward_return_15m": Decimal("0.30")},
    }
    result = compute_forward_relative_returns(forward, ["15m"])
    assert result[1]["forward_relative_return_15m"] == Decimal("0.10") - Decimal("0.20")
    assert result[2]["forward_relative_return_15m"] == Decimal("0")
    assert result[3]["forward_relative_return_15m"] == Decimal("0.30") - Decimal("0.20")


def test_median_is_robust_to_one_extreme_outlier():
    """A single wild memecoin move must not distort every other
    instrument's relative return — the whole reason median, not mean,
    was chosen as the V1 cross-sectional reference."""
    forward = {
        1: {"forward_return_15m": Decimal("0.01")},
        2: {"forward_return_15m": Decimal("0.02")},
        3: {"forward_return_15m": Decimal("4.00")},  # +400% outlier
    }
    result = compute_forward_relative_returns(forward, ["15m"])
    # median is 0.02 (the middle value) — completely unmoved by the outlier.
    assert result[1]["forward_relative_return_15m"] == Decimal("0.01") - Decimal("0.02")


def test_instruments_with_a_none_label_are_excluded_from_the_median_not_treated_as_zero():
    forward = {
        1: {"forward_return_15m": Decimal("0.10")},
        2: {"forward_return_15m": None},  # e.g. delisted before T+H
        3: {"forward_return_15m": Decimal("0.30")},
    }
    result = compute_forward_relative_returns(forward, ["15m"])
    # median of {0.10, 0.30} = 0.20 — instrument 2 never pulled it toward 0.
    assert result[1]["forward_relative_return_15m"] == Decimal("0.10") - Decimal("0.20")
    assert result[2]["forward_relative_return_15m"] is None
    assert result[3]["forward_relative_return_15m"] == Decimal("0.30") - Decimal("0.20")


def test_all_none_labels_yields_all_none_relative_returns_never_a_fabricated_reference():
    forward = {1: {"forward_return_15m": None}, 2: {"forward_return_15m": None}}
    result = compute_forward_relative_returns(forward, ["15m"])
    assert result[1]["forward_relative_return_15m"] is None
    assert result[2]["forward_relative_return_15m"] is None


def test_empty_input_returns_empty_dict():
    assert compute_forward_relative_returns({}, ["15m"]) == {}
