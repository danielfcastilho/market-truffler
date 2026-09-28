from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.bybit.client import BybitApiError
from app.models.funding_rate import FundingRateObservation
from app.models.instrument import Instrument
from app.repositories.funding_rate_repository import FundingRateRepository
from app.services.funding_rate_reconciler import FundingRateReconciler

REQUIRED_WARMUP = timedelta(hours=24)  # 3 eight-hour settlements


def _entry(funding_time: datetime, rate: str = "0.0001", symbol: str = "BTCUSDT") -> dict:
    return {
        "symbol": symbol,
        "fundingRate": rate,
        "fundingRateTimestamp": str(int(funding_time.timestamp() * 1000)),
    }


def _page(entries: list[dict]) -> dict:
    return {"category": "linear", "list": entries}


class _FakeBybitClient:
    def __init__(
        self,
        responses_by_symbol: dict[str, list[dict]] | None = None,
        error: BybitApiError | None = None,
    ) -> None:
        self._queues = {k: list(v) for k, v in (responses_by_symbol or {}).items()}
        self._error = error
        self.calls: list[dict] = []

    async def get_funding_rate_history(self, category, symbol, *, start=None, end=None, limit=200):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "limit": limit})
        if self._error is not None:
            raise self._error
        queue = self._queues.get(symbol, [])
        if queue:
            return queue.pop(0)
        return {"category": category, "list": []}


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_instrument(
    session_factory,
    *,
    symbol: str = "BTCUSDT",
    first_seen_at: datetime,
    funding_synced_from: datetime | None = None,
) -> int:
    async with session_factory() as session:
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol.removesuffix("USDT"),
            quote_coin="USDT",
            is_active=True,
            first_seen_at=first_seen_at,
            last_seen_at=first_seen_at,
            funding_synced_from=funding_synced_from,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_observation(
    session_factory, instrument_id: int, funding_time: datetime, value: str = "0.0001"
) -> None:
    async with session_factory() as session:
        await FundingRateRepository(session).upsert_many(
            [
                {
                    "instrument_id": instrument_id,
                    "funding_time": funding_time,
                    "funding_rate": Decimal(value),
                }
            ]
        )


async def _stored(session_factory, instrument_id: int) -> list[FundingRateObservation]:
    async with session_factory() as session:
        result = await session.execute(
            select(FundingRateObservation).where(
                FundingRateObservation.instrument_id == instrument_id
            )
        )
        return list(result.scalars().all())


def _reconciler(session_factory, bybit_client, **overrides) -> FundingRateReconciler:
    kwargs = {
        "required_warmup": REQUIRED_WARMUP,
        "retention": timedelta(days=2),
        "poll_interval_seconds": 300,
        "max_concurrent_instruments": 5,
        "request_delay_seconds": 0,
    }
    kwargs.update(overrides)
    return FundingRateReconciler(session_factory, bybit_client, **kwargs)


# -- retention/required_warmup invariant ----------------------------------------


def test_default_retention_always_exceeds_required_warmup(session_factory):
    client = _FakeBybitClient()
    deep_warmup = timedelta(days=15)
    reconciler = FundingRateReconciler(session_factory, client, required_warmup=deep_warmup)
    assert reconciler._retention > deep_warmup


def test_an_explicit_retention_override_is_still_honored(session_factory):
    client = _FakeBybitClient()
    reconciler = FundingRateReconciler(
        session_factory, client, required_warmup=timedelta(hours=1), retention=timedelta(days=9)
    )
    assert reconciler._retention == timedelta(days=9)


# -- bootstrap -----------------------------------------------------------------


async def test_bootstrap_completes_in_one_pass_when_bybit_covers_the_whole_range(session_factory):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)

    entries = [_entry(now - timedelta(hours=8 * i)) for i in range(4)]  # 0..24h back
    client = _FakeBybitClient({"BTCUSDT": [_page(entries)]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 4

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.funding_synced_from is not None
        assert refreshed.funding_synced_from <= now - REQUIRED_WARMUP


async def test_bootstrap_already_complete_makes_no_rest_call(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(
        session_factory,
        first_seen_at=now,
        funding_synced_from=now - REQUIRED_WARMUP - timedelta(hours=1),
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    assert client.calls == []


async def test_bootstrap_reaches_natural_floor_on_empty_page(session_factory):
    """A brand-new listing: Bybit has nothing before its own floor —
    accept that as the frontier rather than retrying forever."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient({"BTCUSDT": [_page([])]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.funding_synced_from is not None
        assert refreshed.funding_synced_from >= now - REQUIRED_WARMUP
        upper_bound = datetime.now(UTC) - REQUIRED_WARMUP + timedelta(seconds=30)
        assert refreshed.funding_synced_from <= upper_bound

    assert await _stored(session_factory, iid) == []


async def test_bootstrap_resumes_incrementally_without_redownloading_covered_range(session_factory):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    # Already walked back 8 of the 24 required hours.
    iid = await _seed_instrument(
        session_factory, first_seen_at=now, funding_synced_from=now - timedelta(hours=8)
    )
    entries = [_entry(now - timedelta(hours=16 + 8 * i)) for i in range(2)]  # 16..24h back
    client = _FakeBybitClient({"BTCUSDT": [_page(entries)]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    assert len(client.calls) == 1
    stored = await _stored(session_factory, iid)
    assert len(stored) == 2

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.funding_synced_from <= now - REQUIRED_WARMUP


async def test_bootstrap_survives_a_bybit_failure(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient(error=BybitApiError("boom"))
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")  # must not raise

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.funding_synced_from is None  # unchanged — retried next round


# -- catch-up --------------------------------------------------------------------


async def test_catchup_fetches_only_whats_new_since_the_last_observation(session_factory):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    await _seed_observation(session_factory, iid, now - timedelta(hours=16))

    new_entry = _entry(now - timedelta(hours=8))
    client = _FakeBybitClient({"BTCUSDT": [_page([new_entry])]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 2
    assert client.calls[0]["start"] == int((now - timedelta(hours=16)).timestamp() * 1000) + 1


async def test_catchup_is_a_noop_when_nothing_bootstrapped_yet(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    assert client.calls == []


async def test_catchup_retried_twice_is_idempotent(session_factory):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    await _seed_observation(session_factory, iid, now - timedelta(hours=16))

    new_entry = _entry(now - timedelta(hours=8))
    client = _FakeBybitClient({"BTCUSDT": [_page([new_entry]), _page([new_entry])]})
    reconciler = _reconciler(session_factory, client)

    for _ in range(2):
        async with session_factory() as session:
            instrument = await session.get(Instrument, iid)
            repo = FundingRateRepository(session)
            await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 2  # the original seed + the one new settlement, never duplicated


async def test_catchup_handles_a_dense_funding_interval_just_like_a_sparse_one(session_factory):
    """Different instruments' real intervals (1h vs 8h) are never
    hardcoded or assumed — catch-up simply persists whatever Bybit
    reports for each symbol."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    await _seed_observation(session_factory, iid, now - timedelta(hours=3))

    hourly_entries = [_entry(now - timedelta(hours=2 - i)) for i in range(3)]  # -2h, -1h, 0h
    client = _FakeBybitClient({"BTCUSDT": [_page(hourly_entries)]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = FundingRateRepository(session)
        await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 4  # the original seed plus 3 new hourly settlements


# -- full round / lifecycle -------------------------------------------------------


async def test_process_instrument_survives_a_bad_symbol_without_stopping_the_round(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient(error=BybitApiError("boom"))
    reconciler = _reconciler(session_factory, client)

    import asyncio

    await reconciler._process_instrument(iid, asyncio.Semaphore(1))  # must not raise


async def test_process_instrument_skips_an_inactive_instrument(session_factory):
    async with session_factory() as session:
        now = datetime.now(UTC)
        row = Instrument(
            exchange="bybit",
            symbol="DELISTEDUSDT",
            base_coin="DELISTED",
            quote_coin="USDT",
            is_active=False,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        iid = row.id

    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    import asyncio

    await reconciler._process_instrument(iid, asyncio.Semaphore(1))

    assert client.calls == []


async def test_poll_once_prunes_observations_older_than_retention(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now, funding_synced_from=now)
    await _seed_observation(session_factory, iid, now - timedelta(days=5))
    await _seed_observation(session_factory, iid, now)

    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, retention=timedelta(days=2))

    await reconciler._poll_once()

    stored = await _stored(session_factory, iid)
    assert len(stored) == 1
    assert stored[0].funding_time == now


async def test_poll_once_updates_status(session_factory):
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    assert reconciler.status.last_poll_at is None

    await reconciler._poll_once()

    assert reconciler.status.last_poll_at is not None
    assert reconciler.status.last_poll_ok is True


async def test_start_and_stop_is_clean_with_an_empty_universe(session_factory):
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, poll_interval_seconds=3600)

    await reconciler.start()
    assert reconciler.running
    await reconciler.stop()
    assert not reconciler.running
