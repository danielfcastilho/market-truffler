"""Bounded repository reads (2026-10-26 performance audit): each must return
exactly what the old full-history read returned — including stale anchors,
gaps and availability — while only reading the range that can matter."""

# ruff: noqa: F811 - pytest fixtures are imported and then requested by name

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import update
from test_candle_repository import _row as candle_row
from test_candle_repository import _t, instrument_id  # noqa: F401 - fixture
from test_open_interest_repository import BASE, second_instrument_id  # noqa: F401 - fixture
from test_open_interest_repository import _row as oi_row
from test_open_interest_repository import instrument_id as oi_instrument_id  # noqa: F401

from app.models.open_interest import OpenInterestObservation
from app.repositories.candle_repository import RECENT_CANDLES, CandleRepository
from app.repositories.open_interest_repository import RECENT_OBSERVATIONS, OpenInterestRepository


class _Counting:
    """Counts statements sent through a session."""

    def __init__(self, session):
        self.session = session
        self.calls = 0
        self._execute = session.execute

        async def execute(*args, **kwargs):
            self.calls += 1
            return await self._execute(*args, **kwargs)

        session.execute = execute


async def test_latest_closed_finds_a_stale_anchor_beyond_the_recent_range(
    db_session, instrument_id
):
    repo = CandleRepository(db_session)
    await repo.upsert_many([candle_row(instrument_id, 0)])  # far older than RECENT_CANDLES bars
    bound = _t(RECENT_CANDLES * 3)
    latest = await repo.fetch_latest_closed_per_instrument("5m", [instrument_id], bound)
    assert latest[instrument_id].open_time == _t(0)


async def test_latest_closed_reads_history_only_for_instruments_without_recent_candles(
    db_session,
    instrument_id,
):
    repo = CandleRepository(db_session)
    await repo.upsert_many([candle_row(instrument_id, i) for i in range(5)])
    counter = _Counting(db_session)
    latest = await repo.fetch_latest_closed_per_instrument("5m", [instrument_id], _t(4))
    assert latest[instrument_id].open_time == _t(4)
    assert counter.calls == 1  # found in the recent range: no full-history read
    missing = await repo.fetch_latest_closed_per_instrument("5m", [instrument_id, 999], _t(4))
    assert set(missing) == {instrument_id} and counter.calls == 3  # history read for 999 only


async def test_window_reads_only_the_bars_ending_at_the_anchor(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([candle_row(instrument_id, i) for i in range(40)])
    window = await repo.fetch_window_per_instrument("5m", [instrument_id], _t(30), 15)
    assert [c.open_time for c in window[instrument_id]] == [_t(i) for i in range(16, 31)]


async def test_window_with_a_gap_stays_incomplete(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([candle_row(instrument_id, i) for i in range(40) if i != 20])
    window = await repo.fetch_window_per_instrument("5m", [instrument_id], _t(30), 15)
    # The old "15 most recent" read returned 15 candles reaching back past
    # the gap; both are rejected by the exact-window check.
    assert len(window[instrument_id]) == 14 and _t(20) not in [
        c.open_time for c in window[instrument_id]
    ]


async def test_a_cached_view_reads_a_repeated_window_once_and_the_plain_repository_never_caches(
    db_session,
    instrument_id,
):
    repo = CandleRepository(db_session)
    await repo.upsert_many([candle_row(instrument_id, i) for i in range(20)])
    counter = _Counting(db_session)
    cached = repo.cached()
    a = await cached.fetch_window_per_instrument("5m", [instrument_id], _t(19), 15)
    b = await cached.fetch_window_per_instrument("5m", [instrument_id], _t(19), 15)
    assert a is b and counter.calls == 1
    await cached.fetch_window_per_instrument("5m", [instrument_id], _t(18), 15)
    assert counter.calls == 2  # a different anchor is a different read
    await repo.fetch_window_per_instrument("5m", [instrument_id], _t(19), 15)
    await repo.fetch_window_per_instrument("5m", [instrument_id], _t(19), 15)
    assert counter.calls == 4
    assert repo.cached()._cache == {}  # every view starts empty


async def test_latest_oi_finds_a_stale_observation_and_respects_availability(
    db_session,
    oi_instrument_id,
    second_instrument_id,
):
    repo = OpenInterestRepository(db_session)
    stale = int((RECENT_OBSERVATIONS * 3).total_seconds() // 60)
    late = datetime(2026, 6, 1, tzinfo=UTC)  # stored long after it was observed
    await repo.upsert_many([oi_row(oi_instrument_id, 0), oi_row(second_instrument_id, stale)])
    await db_session.execute(
        update(OpenInterestObservation)
        .where(OpenInterestObservation.instrument_id == oi_instrument_id)
        .values(ingested_at=late)
    )
    await db_session.commit()
    bound = BASE + timedelta(minutes=stale)
    latest = await repo.fetch_latest_at_or_before_per_instrument(
        [oi_instrument_id, second_instrument_id], bound
    )
    assert latest[oi_instrument_id].observed_at == BASE  # beyond the recent range: still found
    assert latest[second_instrument_id].observed_at == bound
    # Stored after the decision time: not available, in either phase.
    unavailable = await repo.fetch_latest_at_or_before_per_instrument(
        [oi_instrument_id], bound, available_by=late - timedelta(days=1)
    )
    assert unavailable == {}


async def test_oi_anchor_is_read_once_per_cached_view(db_session, oi_instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([oi_row(oi_instrument_id, m, value=str(1000 + m)) for m in (0, 5, 10)])
    counter = _Counting(db_session)
    cached = repo.cached()
    bound = BASE + timedelta(minutes=10)
    first = await cached.fetch_latest_at_or_before_per_instrument([oi_instrument_id], bound)
    for _ in range(4):  # the other four oi_change_* features
        assert (
            await cached.fetch_latest_at_or_before_per_instrument([oi_instrument_id], bound)
            is first
        )
    assert counter.calls == 1
    assert first[oi_instrument_id].open_interest == Decimal("1010")
