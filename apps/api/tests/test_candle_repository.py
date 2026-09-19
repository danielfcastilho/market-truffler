from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository


@pytest.fixture
async def instrument_id(db_session) -> int:
    now = datetime.now(UTC)
    row = Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
        history_target_start=now,
        history_synced_from=now,
        history_synced_through=now,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


def _row(instrument_id: int, minute: int, close: str = "100.0") -> dict:
    open_time = datetime(2026, 1, 1, 0, minute, tzinfo=UTC)
    return {
        "instrument_id": instrument_id,
        "timeframe": "1m",
        "open_time": open_time,
        "close_time": open_time.replace(second=59, microsecond=999000),
        "open": Decimal("100.0"),
        "high": Decimal("101.0"),
        "low": Decimal("99.0"),
        "close": Decimal(close),
        "volume": Decimal("10.0"),
        "turnover": Decimal("1000.0"),
    }


async def test_closed_candle_persists_correctly(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1
    assert stored[0].instrument_id == instrument_id
    assert stored[0].timeframe == "1m"
    assert stored[0].close == Decimal("100.0")


async def test_duplicate_ingestion_is_idempotent(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])
    await repo.upsert_many([_row(instrument_id, 0)])  # exact duplicate

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1


async def test_candle_uniqueness_enforced_via_upsert_correction(db_session, instrument_id):
    """A second write for the same (instrument, timeframe, open_time) key
    overwrites rather than duplicating — the composite primary key enforces
    uniqueness, and the upsert path treats a differing value as a
    correction (M3 section 15)."""
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0, close="100.0")])
    await repo.upsert_many([_row(instrument_id, 0, close="105.5")])  # corrected value

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 1
    assert stored[0].close == Decimal("105.5")


async def test_upsert_many_batches_multiple_rows_in_one_call(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, m) for m in range(5)])

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert len(stored) == 5


async def test_upsert_many_is_a_noop_for_an_empty_batch(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([])  # must not raise

    stored = (await db_session.execute(select(Candle))).scalars().all()
    assert stored == []


async def test_fetch_range_returns_only_rows_within_bounds_ordered(db_session, instrument_id):
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, m) for m in range(10)])

    rows = await repo.fetch_range(
        instrument_id,
        "1m",
        datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    )

    assert [r.open_time.minute for r in rows] == [2, 3, 4]


@pytest.fixture
async def second_instrument_id(db_session) -> int:
    now = datetime.now(UTC)
    row = Instrument(
        exchange="bybit",
        symbol="ETHUSDT",
        base_coin="ETH",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


class TestFetchLatestClosedPerInstrument:
    """M4 temporal correctness: `fetch_latest_closed_per_instrument` is the
    core query the frame synchronizer relies on to never leak a
    still-forming (or simply too-new) candle into a frame."""

    async def test_returns_the_latest_candle_at_or_before_the_bound(
        self, db_session, instrument_id
    ):
        repo = CandleRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in range(5)])  # minutes 0-4

        latest = await repo.fetch_latest_closed_per_instrument(
            "1m", [instrument_id], datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
        )

        assert latest[instrument_id].open_time == datetime(2026, 1, 1, 0, 4, tzinfo=UTC)

    async def test_a_candle_newer_than_the_bound_never_leaks_in(self, db_session, instrument_id):
        """The exact invariant M4 depends on: a row that exists in the
        database but closes after the requested bound must never be
        selected, however recently it was written."""
        repo = CandleRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in range(5)])  # minutes 0-4

        latest = await repo.fetch_latest_closed_per_instrument(
            "1m", [instrument_id], datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        )

        assert latest[instrument_id].open_time == datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        assert latest[instrument_id].open_time < datetime(2026, 1, 1, 0, 4, tzinfo=UTC)

    async def test_instrument_with_no_candle_at_or_before_the_bound_is_absent(
        self, db_session, instrument_id
    ):
        repo = CandleRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 5)])  # only minute 5, after the bound

        latest = await repo.fetch_latest_closed_per_instrument(
            "1m", [instrument_id], datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
        )

        assert instrument_id not in latest

    async def test_resolves_the_whole_instrument_set_independently_in_one_call(
        self, db_session, instrument_id, second_instrument_id
    ):
        repo = CandleRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in range(5)])  # minutes 0-4
        await repo.upsert_many(
            [
                {**_row(second_instrument_id, m), "instrument_id": second_instrument_id}
                for m in range(2)  # only minutes 0-1
            ]
        )

        latest = await repo.fetch_latest_closed_per_instrument(
            "1m", [instrument_id, second_instrument_id], datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
        )

        assert latest[instrument_id].open_time == datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
        assert latest[second_instrument_id].open_time == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

    async def test_empty_instrument_list_returns_empty_without_querying(self, db_session):
        repo = CandleRepository(db_session)
        assert await repo.fetch_latest_closed_per_instrument("1m", [], datetime.now(UTC)) == {}

    async def test_uses_exactly_one_query_regardless_of_instrument_count(self, db_session):
        """M4 performance requirement: resolving an entire universe's latest
        legal candle for one timeframe must be one set-oriented query, never
        a per-instrument loop."""
        repo = CandleRepository(db_session)
        instrument_ids = []
        for i in range(25):
            now = datetime.now(UTC)
            row = Instrument(
                exchange="bybit",
                symbol=f"SYM{i}USDT",
                base_coin=f"SYM{i}",
                quote_coin="USDT",
                is_active=True,
                first_seen_at=now,
                last_seen_at=now,
            )
            db_session.add(row)
        await db_session.commit()
        result = await db_session.execute(select(Instrument.id))
        instrument_ids = [r[0] for r in result]

        rows = [{**_row(iid, 0), "instrument_id": iid} for iid in instrument_ids]
        await repo.upsert_many(rows)

        real_execute = db_session.execute
        spy = AsyncMock(side_effect=real_execute)
        db_session.execute = spy

        await repo.fetch_latest_closed_per_instrument(
            "1m", instrument_ids, datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        )

        assert spy.await_count == 1


async def test_fetch_range_excludes_a_still_forming_candle_at_the_upper_bound(
    db_session, instrument_id
):
    """`fetch_range`'s `end` is exclusive — a candle open exactly at `end`
    must never be treated as available (it represents the moment a
    higher-timeframe window closes, not a candle inside it)."""
    repo = CandleRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 5)])

    rows = await repo.fetch_range(
        instrument_id,
        "1m",
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    )

    assert rows == []
