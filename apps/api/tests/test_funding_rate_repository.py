from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.funding_rate import FundingRateObservation
from app.models.instrument import Instrument
from app.repositories.funding_rate_repository import FundingRateRepository

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


def _row(instrument_id: int, hours: int, value: str = "0.0001") -> dict:
    return {
        "instrument_id": instrument_id,
        "funding_time": BASE + timedelta(hours=hours),
        "funding_rate": Decimal(value),
    }


async def test_observation_persists_correctly(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(FundingRateObservation))).scalars().all()
    assert len(stored) == 1
    assert stored[0].instrument_id == instrument_id
    assert stored[0].funding_rate == Decimal("0.0001")


async def test_duplicate_ingestion_is_idempotent(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0)])
    await repo.upsert_many([_row(instrument_id, 0)])

    stored = (await db_session.execute(select(FundingRateObservation))).scalars().all()
    assert len(stored) == 1


async def test_a_repeated_observation_corrects_the_value(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.upsert_many([_row(instrument_id, 0, value="0.0001")])
    await repo.upsert_many([_row(instrument_id, 0, value="0.0002")])

    stored = (await db_session.execute(select(FundingRateObservation))).scalars().all()
    assert len(stored) == 1
    assert stored[0].funding_rate == Decimal("0.0002")


async def test_upsert_many_is_a_noop_for_an_empty_batch(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.upsert_many([])

    stored = (await db_session.execute(select(FundingRateObservation))).scalars().all()
    assert stored == []


async def test_a_zero_or_negative_rate_persists_exactly_like_any_other_value(
    db_session, instrument_id
):
    """Unlike Open Interest, funding rate has no "must be positive"
    invariant — zero and negative rates are ordinary, legitimate readings."""
    repo = FundingRateRepository(db_session)
    await repo.upsert_many(
        [_row(instrument_id, 0, value="0"), _row(instrument_id, 8, value="-0.0005")]
    )

    stored = {
        s.funding_time: s.funding_rate
        for s in (await db_session.execute(select(FundingRateObservation))).scalars().all()
    }
    assert stored[BASE] == Decimal("0")
    assert stored[BASE + timedelta(hours=8)] == Decimal("-0.0005")


class TestFetchLatestAtOrBeforePerInstrument:
    async def test_returns_the_latest_observation_at_or_before_the_bound(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(hours=16)
        )

        assert latest[instrument_id].funding_time == BASE + timedelta(hours=16)

    async def test_an_observation_after_the_bound_never_leaks_in(self, db_session, instrument_id):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(hours=12)
        )

        assert latest[instrument_id].funding_time == BASE + timedelta(hours=8)

    async def test_instrument_with_nothing_at_or_before_the_bound_is_absent(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 16)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(hours=8)
        )

        assert instrument_id not in latest

    async def test_resolves_the_whole_instrument_set_independently_in_one_call(
        self, db_session, instrument_id, second_instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16)])
        await repo.upsert_many([_row(second_instrument_id, h) for h in (0, 8)])

        latest = await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id, second_instrument_id], BASE + timedelta(hours=16)
        )

        assert latest[instrument_id].funding_time == BASE + timedelta(hours=16)
        assert latest[second_instrument_id].funding_time == BASE + timedelta(hours=8)

    async def test_empty_instrument_list_returns_empty_without_querying(self, db_session):
        repo = FundingRateRepository(db_session)
        assert await repo.fetch_latest_at_or_before_per_instrument([], datetime.now(UTC)) == {}

    async def test_uses_exactly_one_query_regardless_of_instrument_count(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 0)])

        real_execute = db_session.execute
        spy = AsyncMock(side_effect=real_execute)
        db_session.execute = spy

        await repo.fetch_latest_at_or_before_per_instrument(
            [instrument_id], BASE + timedelta(hours=16)
        )

        assert spy.await_count == 1


class TestFetchSincePerInstrument:
    async def test_returns_only_observations_within_the_inclusive_window(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16, 24, 32)])

        window = await repo.fetch_since_per_instrument(
            [instrument_id], BASE + timedelta(hours=8), BASE + timedelta(hours=24)
        )

        assert [o.funding_time for o in window[instrument_id]] == [
            BASE + timedelta(hours=8),
            BASE + timedelta(hours=16),
            BASE + timedelta(hours=24),
        ]

    async def test_instrument_with_no_observations_in_the_window_is_absent(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, 0)])

        window = await repo.fetch_since_per_instrument(
            [instrument_id], BASE + timedelta(hours=100), BASE + timedelta(hours=124)
        )

        assert instrument_id not in window

    async def test_a_denser_funding_interval_naturally_yields_more_points_in_the_same_window(
        self, db_session, instrument_id, second_instrument_id
    ):
        """Different instruments settling at different real intervals
        (e.g. 1h vs 8h) must each just get however many observations
        actually fall in the window — never normalized to a fixed count."""
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in range(0, 24, 1)])  # hourly
        await repo.upsert_many([_row(second_instrument_id, h) for h in range(0, 24, 8)])  # 8h

        window = await repo.fetch_since_per_instrument(
            [instrument_id, second_instrument_id], BASE, BASE + timedelta(hours=23)
        )

        assert len(window[instrument_id]) == 24
        assert len(window[second_instrument_id]) == 3

    async def test_empty_instrument_list_returns_empty(self, db_session):
        repo = FundingRateRepository(db_session)
        assert await repo.fetch_since_per_instrument([], BASE, BASE) == {}


class TestFetchLatestFundingTimePerInstrument:
    async def test_returns_the_max_funding_time_per_instrument(
        self, db_session, instrument_id, second_instrument_id
    ):
        repo = FundingRateRepository(db_session)
        await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16)])
        await repo.upsert_many([_row(second_instrument_id, 0)])

        latest = await repo.fetch_latest_funding_time_per_instrument(
            [instrument_id, second_instrument_id]
        )

        assert latest[instrument_id] == BASE + timedelta(hours=16)
        assert latest[second_instrument_id] == BASE

    async def test_instrument_with_no_observations_at_all_is_absent(
        self, db_session, instrument_id
    ):
        repo = FundingRateRepository(db_session)
        latest = await repo.fetch_latest_funding_time_per_instrument([instrument_id])
        assert instrument_id not in latest

    async def test_empty_instrument_list_returns_empty(self, db_session):
        repo = FundingRateRepository(db_session)
        assert await repo.fetch_latest_funding_time_per_instrument([]) == {}


async def test_delete_before_prunes_only_older_rows(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.upsert_many([_row(instrument_id, h) for h in (0, 8, 16)])

    await repo.delete_before(BASE + timedelta(hours=8))

    remaining = (await db_session.execute(select(FundingRateObservation))).scalars().all()
    assert {r.funding_time for r in remaining} == {
        BASE + timedelta(hours=8),
        BASE + timedelta(hours=16),
    }


async def test_delete_before_is_harmless_with_nothing_to_prune(db_session, instrument_id):
    repo = FundingRateRepository(db_session)
    await repo.delete_before(datetime.now(UTC))  # must not raise
