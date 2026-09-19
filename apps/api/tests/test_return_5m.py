from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.frame import FrameCandleContext, FrameStatus, MarketFrame, MarketFrameMember
from app.features.return_5m import Return5m
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
CURRENT_OPEN = FRAME_TIME - timedelta(minutes=1)  # 13:59 — the latest legal 1m candle


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


# -- formula --------------------------------------------------------------------


async def test_formula_100_to_101_is_plus_one_percent(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("101") / Decimal("100") - 1
    assert result[iid] == Decimal("0.01")


async def test_formula_100_to_99_is_minus_one_percent(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
    frame = _frame([_member(iid, "BTCUSDT", "99")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("99") / Decimal("100") - 1
    assert result[iid] == Decimal("-0.01")


async def test_formula_unchanged_price_is_zero(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
    frame = _frame([_member(iid, "BTCUSDT", "100")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("0")


async def test_formula_exact_decimal_precision(db_session):
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "154")
    frame = _frame([_member(iid, "BTCUSDT", "159")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("159") / Decimal("154") - 1


# -- temporal semantics -----------------------------------------------------------


async def test_uses_exactly_the_5_minute_boundary_not_4_or_6(db_session):
    iid = await _seed_instrument(db_session)
    # Candles at 4, 5, and 6 minutes before "current" — only the exact
    # 5-minute one may be used.
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=4), "999")
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=6), "111")
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("101") / Decimal("100") - 1  # only the exact 100 was used


async def test_current_close_comes_from_the_frame_not_a_fresh_query(db_session):
    """A newer 1m candle sitting in market_candles (arrived after frame T
    was finalized) must never leak in as "current" — the frame's own
    selected m1 context is authoritative."""
    iid = await _seed_instrument(db_session)
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
    # A newer candle than the frame's own selection — must be ignored.
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN + timedelta(minutes=1), "99999")
    frame = _frame([_member(iid, "BTCUSDT", "101")])  # frame says "current" close is 101

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] == Decimal("101") / Decimal("100") - 1


async def test_a_future_candle_at_the_lookback_offset_does_not_leak_in(db_session):
    """A row exists at the exact target open_time is required — but nothing
    from strictly after the frame's current candle may ever be selected as
    the *current* value (covered above); this proves the historical lookup
    itself is exact-match, not "closest available", so a candle inserted
    for a completely different (future) time cannot substitute."""
    iid = await _seed_instrument(db_session)
    # No candle at exactly T-5m — only one further in the future exists.
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN + timedelta(minutes=10), "100")
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] is None  # unavailable, never substituted


# -- missing data -----------------------------------------------------------------


async def test_missing_exact_t_minus_5_candle_is_unavailable(db_session):
    iid = await _seed_instrument(db_session)
    # Nothing seeded at all.
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_does_not_substitute_the_nearest_candle(db_session):
    iid = await _seed_instrument(db_session)
    # Candles at 3 and 7 minutes ago — neither is the exact 5-minute mark.
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=3), "50")
    await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=7), "70")
    frame = _frame([_member(iid, "BTCUSDT", "101")])

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[iid] is None


async def test_empty_frame_returns_empty_result(db_session):
    frame = _frame([])
    result = await Return5m().calculate(frame, CandleRepository(db_session))
    assert result == {}


# -- batch execution ----------------------------------------------------------------


async def test_multiple_instruments_calculated_correctly_in_one_call(db_session):
    btc = await _seed_instrument(db_session, "BTCUSDT")
    eth = await _seed_instrument(db_session, "ETHUSDT")
    sol = await _seed_instrument(db_session, "SOLUSDT")
    await _seed_1m_candle(db_session, btc, CURRENT_OPEN - timedelta(minutes=5), "100")
    await _seed_1m_candle(db_session, eth, CURRENT_OPEN - timedelta(minutes=5), "200")
    # sol has no historical candle at all -> unavailable
    frame = _frame(
        [
            _member(btc, "BTCUSDT", "101"),
            _member(eth, "ETHUSDT", "196"),
            _member(sol, "SOLUSDT", "50"),
        ]
    )

    result = await Return5m().calculate(frame, CandleRepository(db_session))

    assert result[btc] == Decimal("101") / Decimal("100") - 1
    assert result[eth] == Decimal("196") / Decimal("200") - 1
    assert result[sol] is None


async def test_historical_lookup_is_batched_not_per_instrument(db_session):
    """M5 section 12: resolving the historical comparison candle for many
    instruments sharing the same anchor time must be one query, not N."""
    from unittest.mock import AsyncMock

    ids = []
    for i in range(25):
        iid = await _seed_instrument(db_session, f"SYM{i}USDT")
        await _seed_1m_candle(db_session, iid, CURRENT_OPEN - timedelta(minutes=5), "100")
        ids.append(iid)
    frame = _frame([_member(iid, f"SYM{i}USDT", "101") for i, iid in enumerate(ids)])

    candle_repo = CandleRepository(db_session)
    real_execute = db_session.execute
    spy = AsyncMock(side_effect=real_execute)
    db_session.execute = spy

    result = await Return5m().calculate(frame, candle_repo)

    assert len(result) == 25
    assert all(v == Decimal("0.01") for v in result.values())
    assert spy.await_count == 1  # one shared anchor time -> one batched query
