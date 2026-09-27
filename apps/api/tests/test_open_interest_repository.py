from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.instrument import Instrument
from app.models.open_interest import OpenInterestObservation
from app.repositories.open_interest_repository import OpenInterestRepository

BASE = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


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
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


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


def _row(instrument_id: int, minutes: int, value: str = "1000") -> dict:
    return {
        "instrument_id": instrument_id,
        "observed_at": BASE + timedelta(minutes=minutes),
        "open_interest": Decimal(value),
    }


async def test_observation_persists_correctly(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(OpenInterestObservation))).scalars().all()
    assert len(stored) == 1
    assert stored[0].instrument_id == instrument_id
    assert stored[0].open_interest == Decimal("1000")


async def test_duplicate_ingestion_is_idempotent(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(OpenInterestObservation))).scalars().all()
    assert len(stored) == 1


async def test_a_repeated_observation_corrects_the_value(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0, value="1000")])
    await repo.upsert_many([_row(instrument_id, 0, value="1050")])

    stored = (await db_session.execute(select(OpenInterestObservation))).scalars().all()
    assert len(stored) == 1
    assert stored[0].open_interest == Decimal("1050")


async def test_upsert_many_is_a_noop_for_an_empty_batch(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([])

    stored = (await db_session.execute(select(OpenInterestObservation))).scalars().all()
    assert stored == []


class TestFetchLatestAtOrBeforePerInstrument:
    async def test_returns_the_latest_observation_at_or_before_the_bound(
        self, db_session, instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(minutes=10)
        )

        assert latest[instrument_id].observed_at == BASE + timedelta(minutes=10)

    async def test_an_observation_after_the_bound_never_leaks_in(self, db_session, instrument_id):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(minutes=7)
        )

        assert latest[instrument_id].observed_at == BASE + timedelta(minutes=5)

    async def test_instrument_with_nothing_at_or_before_the_bound_is_absent(
        self, db_session, instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 10)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(minutes=5)
        )

        assert instrument_id not in latest

    async def test_resolves_the_whole_instrument_set_independently_in_one_call(
        self, db_session, instrument_id, second_instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])
        await repo.upsert_many([_row(second_instrument_id, m) for m in (0, 5)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id, second_instrument_id], BASE + timedelta(minutes=10)
        )

        assert latest[instrument_id].observed_at == BASE + timedelta(minutes=10)
        assert latest[second_instrument_id].observed_at == BASE + timedelta(minutes=5)

    async def test_empty_instrument_list_returns_empty_without_querying(self, db_session):
        repo = OpenInterestRepository(db_session)
        assert await repo.fetch_latest_at_or_before_per_instrument([], datetime.now(UTC)) == {}

    async def test_uses_exactly_one_query_regardless_of_instrument_count(
        self, db_session, instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 0)])

        real_execute = db_session.execute
        spy = AsyncMock(side_effect=real_execute)
        db_session.execute = spy

        await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(minutes=10)
        )

        assert spy.await_count == 1


class TestFetchExactObservedAt:
    async def test_returns_only_the_exact_match(self, db_session, instrument_id):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])

        result = await repo.fetch_exact_observed_at([instrument_id], BASE + timedelta(minutes=5))

        assert result[instrument_id].observed_at == BASE + timedelta(minutes=5)

    async def test_instrument_with_no_observation_at_that_exact_instant_is_absent(
        self, db_session, instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 0)])

        result = await repo.fetch_exact_observed_at([instrument_id], BASE + timedelta(minutes=3))

        assert instrument_id not in result

    async def test_empty_instrument_list_returns_empty(self, db_session):
        repo = OpenInterestRepository(db_session)
        assert await repo.fetch_exact_observed_at([], BASE) == {}


class TestFetchLatestObservedAtPerInstrument:
    async def test_returns_the_max_observed_at_per_instrument(
        self, db_session, instrument_id, second_instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])
        await repo.upsert_many([_row(second_instrument_id, 0)])

        latest = await repo.fetch_latest_observed_at_per_instrument(
            [instrument_id, second_instrument_id]
        )

        assert latest[instrument_id] == BASE + timedelta(minutes=10)
        assert latest[second_instrument_id] == BASE

    async def test_instrument_with_no_observations_at_all_is_absent(
        self, db_session, instrument_id
    ):
        repo = OpenInterestRepository(db_session)
        latest = await repo.fetch_latest_observed_at_per_instrument([instrument_id])
        assert instrument_id not in latest

    async def test_empty_instrument_list_returns_empty(self, db_session):
        repo = OpenInterestRepository(db_session)
        assert await repo.fetch_latest_observed_at_per_instrument([]) == {}


async def test_delete_before_prunes_only_older_rows(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.upsert_many([_row(instrument_id, m) for m in (0, 5, 10)])

    await repo.delete_before(BASE + timedelta(minutes=5))

    remaining = (await db_session.execute(select(OpenInterestObservation))).scalars().all()
    assert {r.observed_at for r in remaining} == {
        BASE + timedelta(minutes=5),
        BASE + timedelta(minutes=10),
    }


async def test_delete_before_is_harmless_with_nothing_to_prune(db_session, instrument_id):
    repo = OpenInterestRepository(db_session)
    await repo.delete_before(datetime.now(UTC))  # must not raise
