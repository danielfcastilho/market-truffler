from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.bybit.client import BybitApiError
from app.models.instrument import Instrument
from app.models.open_interest import OpenInterestObservation
from app.repositories.open_interest_repository import OpenInterestRepository
from app.services.open_interest_reconciler import OpenInterestReconciler

REQUIRED_WARMUP = timedelta(minutes=30)  # 6 five-minute buckets


def _entry(observed_at: datetime, value: str = "1000") -> dict:
    return {"openInterest": value, "timestamp": str(int(observed_at.timestamp() * 1000))}


def _page(entries: list[dict], symbol: str = "BTCUSDT") -> dict:
    return {"category": "linear", "symbol": symbol, "list": entries}


class _FakeBybitClient:
    def __init__(
        self,
        responses_by_symbol: dict[str, list[dict]] | None = None,
        error: BybitApiError | None = None,
    ) -> None:
        self._queues = {k: list(v) for k, v in (responses_by_symbol or {}).items()}
        self._error = error
        self.calls: list[dict] = []

    async def get_open_interest(
        self,
        category,
        symbol,
        *,
        interval_time="5min",
        start=None,
        end=None,
        cursor=None,
        limit=200,
    ):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "limit": limit})
        if self._error is not None:
            raise self._error
        queue = self._queues.get(symbol, [])
        if queue:
            return queue.pop(0)
        return {"category": category, "symbol": symbol, "list": []}


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_instrument(
    session_factory,
    *,
    symbol: str = "BTCUSDT",
    first_seen_at: datetime,
    oi_synced_from: datetime | None = None,
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
            oi_synced_from=oi_synced_from,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_observation(
    session_factory, instrument_id: int, observed_at: datetime, value: int = 1000
) -> None:
    async with session_factory() as session:
        await OpenInterestRepository(session).upsert_many(
            [{"instrument_id": instrument_id, "observed_at": observed_at, "open_interest": value}]
        )


async def _stored(session_factory, instrument_id: int) -> list[OpenInterestObservation]:
    async with session_factory() as session:
        result = await session.execute(
            select(OpenInterestObservation).where(
                OpenInterestObservation.instrument_id == instrument_id
            )
        )
        return list(result.scalars().all())


def _reconciler(session_factory, bybit_client, **overrides) -> OpenInterestReconciler:
    kwargs = {
        "required_warmup": REQUIRED_WARMUP,
        "retention": timedelta(days=2),
        "poll_interval_seconds": 300,
        "max_concurrent_instruments": 5,
        "request_delay_seconds": 0,
    }
    kwargs.update(overrides)
    return OpenInterestReconciler(session_factory, bybit_client, **kwargs)


# -- retention/required_warmup invariant ----------------------------------------


def test_default_retention_always_exceeds_required_warmup(session_factory):
    """Regression guard: retention must never be shorter than
    required_warmup, or every poll round would prune the very bootstrap
    progress it just walked back to, and no symbol could ever reach OI
    READY. A deep required_warmup (e.g. volatility_24h's 15 days) must
    still get a safely-longer default retention with zero explicit
    configuration."""
    client = _FakeBybitClient()
    deep_warmup = timedelta(days=15)
    reconciler = OpenInterestReconciler(session_factory, client, required_warmup=deep_warmup)
    assert reconciler._retention > deep_warmup


def test_an_explicit_retention_override_is_still_honored(session_factory):
    client = _FakeBybitClient()
    reconciler = OpenInterestReconciler(
        session_factory, client, required_warmup=timedelta(hours=1), retention=timedelta(days=9)
    )
    assert reconciler._retention == timedelta(days=9)


# -- bootstrap -----------------------------------------------------------------


async def test_bootstrap_completes_in_one_pass_when_bybit_covers_the_whole_range(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)

    entries = [_entry(now - timedelta(minutes=5 * i)) for i in range(7)]  # 0..30 min back
    client = _FakeBybitClient({"BTCUSDT": [_page(entries)]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 7

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.oi_synced_from is not None
        assert refreshed.oi_synced_from <= now - REQUIRED_WARMUP


async def test_bootstrap_already_complete_makes_no_rest_call(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(
        session_factory,
        first_seen_at=now,
        oi_synced_from=now - REQUIRED_WARMUP - timedelta(minutes=1),
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    assert client.calls == []


async def test_bootstrap_reaches_natural_floor_on_empty_page(session_factory):
    """A brand-new listing: Bybit has nothing before its own floor —
    accept that as the frontier rather than retrying forever."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient({"BTCUSDT": [_page([])]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.oi_synced_from is not None
        # A relaxed range, not exact equality: the code computes its own
        # fresh `now` internally, slightly later than this test's capture.
        assert refreshed.oi_synced_from >= now - REQUIRED_WARMUP
        upper_bound = datetime.now(UTC) - REQUIRED_WARMUP + timedelta(seconds=30)
        assert refreshed.oi_synced_from <= upper_bound

    assert await _stored(session_factory, iid) == []


async def test_bootstrap_resumes_incrementally_without_redownloading_covered_range(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # Already walked back 15 of the 30 required minutes.
    iid = await _seed_instrument(
        session_factory, first_seen_at=now, oi_synced_from=now - timedelta(minutes=15)
    )
    entries = [_entry(now - timedelta(minutes=15 + 5 * i)) for i in range(4)]  # 15..30 min back
    client = _FakeBybitClient({"BTCUSDT": [_page(entries)]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")

    assert len(client.calls) == 1
    stored = await _stored(session_factory, iid)
    assert len(stored) == 4

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.oi_synced_from <= now - REQUIRED_WARMUP


async def test_bootstrap_survives_a_bybit_failure(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient(error=BybitApiError("boom"))
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._bootstrap_step(session, repo, instrument, "BTCUSDT")  # must not raise

    async with session_factory() as session:
        refreshed = await session.get(Instrument, iid)
        assert refreshed.oi_synced_from is None  # unchanged — retried next round


# -- catch-up --------------------------------------------------------------------


async def test_catchup_fetches_only_whats_new_since_the_last_observation(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    await _seed_observation(session_factory, iid, now - timedelta(minutes=10))

    new_entry = _entry(now - timedelta(minutes=5))
    client = _FakeBybitClient({"BTCUSDT": [_page([new_entry])]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 2
    assert client.calls[0]["start"] == int((now - timedelta(minutes=10)).timestamp() * 1000) + 1


async def test_catchup_is_a_noop_when_nothing_bootstrapped_yet(session_factory):
    now = datetime.now(UTC)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, iid)
        repo = OpenInterestRepository(session)
        await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    assert client.calls == []


async def test_catchup_retried_twice_is_idempotent(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    iid = await _seed_instrument(session_factory, first_seen_at=now)
    await _seed_observation(session_factory, iid, now - timedelta(minutes=10))

    new_entry = _entry(now - timedelta(minutes=5))
    # Same response served twice (simulating a duplicate/retried round).
    client = _FakeBybitClient({"BTCUSDT": [_page([new_entry]), _page([new_entry])]})
    reconciler = _reconciler(session_factory, client)

    for _ in range(2):
        async with session_factory() as session:
            instrument = await session.get(Instrument, iid)
            repo = OpenInterestRepository(session)
            await reconciler._catchup_step(session, repo, instrument, "BTCUSDT")

    stored = await _stored(session_factory, iid)
    assert len(stored) == 2  # the original seed + the one new bucket, never duplicated


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
    iid = await _seed_instrument(session_factory, first_seen_at=now, oi_synced_from=now)
    await _seed_observation(session_factory, iid, now - timedelta(days=5))
    await _seed_observation(session_factory, iid, now)

    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, retention=timedelta(days=2))

    await reconciler._poll_once()

    stored = await _stored(session_factory, iid)
    assert len(stored) == 1
    assert stored[0].observed_at == now


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
