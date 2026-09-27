from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, MarketFrameMember
from app.features.candle_window import REQUIRED_CLOSES, expected_window, fetch_verified_windows
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


def test_expected_window_is_required_closes_consecutive_timestamps_oldest_first():
    anchor = FRAME_TIME
    duration = timedelta(hours=1)
    window = expected_window(anchor, duration)

    assert len(window) == REQUIRED_CLOSES
    assert window[-1] == anchor
    assert window == [anchor - duration * i for i in range(REQUIRED_CLOSES - 1, -1, -1)]
    assert window == sorted(window)  # oldest first


def _ctx(open_time: datetime) -> FrameCandleContext:
    return FrameCandleContext(
        open_time=open_time,
        close_time=open_time + timedelta(hours=1) - timedelta(microseconds=1),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
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


async def _seed_hourly_run(db_session, instrument_id: int, anchor: datetime, count: int) -> None:
    duration = timedelta(hours=1)
    start = anchor - duration * (count - 1)
    repo = CandleRepository(db_session)
    for i in range(count):
        open_time = start + duration * i
        await repo.upsert_many(
            [
                {
                    "instrument_id": instrument_id,
                    "timeframe": "1h",
                    "open_time": open_time,
                    "close_time": open_time + duration - timedelta(microseconds=1),
                    "open": Decimal("1"),
                    "high": Decimal("1"),
                    "low": Decimal("1"),
                    "close": Decimal("1"),
                    "volume": Decimal("1"),
                    "turnover": Decimal("1"),
                }
            ]
        )


def _member(iid: int, anchor: datetime, *, h4=None) -> MarketFrameMember:
    placeholder = _ctx(anchor)
    return MarketFrameMember(
        instrument_id=iid,
        symbol="BTCUSDT",
        m1=placeholder,
        m5=placeholder,
        m15=placeholder,
        h1=placeholder,
        h4=h4,
    )


async def test_fetch_verified_windows_returns_the_full_window_when_complete(db_session):
    iid = await _seed_instrument(db_session, "BTCUSDT")
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_hourly_run(db_session, iid, anchor, REQUIRED_CLOSES)
    member = _member(iid, anchor)

    windows = await fetch_verified_windows(
        CandleRepository(db_session), "1h", timedelta(hours=1), [member], lambda m: m.h1
    )

    assert iid in windows
    assert len(windows[iid]) == REQUIRED_CLOSES
    assert windows[iid][-1].open_time == anchor
    assert [c.open_time for c in windows[iid]] == expected_window(anchor, timedelta(hours=1))


async def test_fetch_verified_windows_excludes_a_short_history(db_session):
    iid = await _seed_instrument(db_session, "BTCUSDT")
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_hourly_run(db_session, iid, anchor, REQUIRED_CLOSES - 1)
    member = _member(iid, anchor)

    windows = await fetch_verified_windows(
        CandleRepository(db_session), "1h", timedelta(hours=1), [member], lambda m: m.h1
    )

    assert iid not in windows


async def test_fetch_verified_windows_excludes_a_member_with_no_anchor(db_session):
    iid = await _seed_instrument(db_session, "BTCUSDT")
    anchor = FRAME_TIME - timedelta(hours=1)
    await _seed_hourly_run(db_session, iid, anchor, REQUIRED_CLOSES)
    member = _member(iid, anchor, h4=None)

    windows = await fetch_verified_windows(
        CandleRepository(db_session), "4h", timedelta(hours=4), [member], lambda m: m.h4
    )

    assert windows == {}


async def test_fetch_verified_windows_returns_empty_for_no_members(db_session):
    windows = await fetch_verified_windows(
        CandleRepository(db_session), "1h", timedelta(hours=1), [], lambda m: m.h1
    )
    assert windows == {}
