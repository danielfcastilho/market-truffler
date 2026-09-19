import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.market import Instrument as DomainInstrument
from app.integrations.bybit.client import BybitApiError
from app.models.candle import Candle
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.services.history_reconciler import HistoryReconciler
from app.services.market_universe import MarketUniverseUnavailable

RETENTION_DAYS = 365


async def _stored_1m(session_factory, instrument_id: int) -> list[Candle]:
    async with session_factory() as session:
        result = await session.execute(
            select(Candle).where(Candle.instrument_id == instrument_id, Candle.timeframe == "1m")
        )
        return list(result.scalars().all())


def _raw_row(open_time: datetime, close: str = "100.0") -> list[str]:
    start_ms = int(open_time.timestamp() * 1000)
    return [str(start_ms), "100.0", "101.0", "99.0", close, "10.0", "1000.0"]


class _FakeBybitClient:
    def __init__(
        self,
        responses_by_symbol: dict[str, list[dict]] | None = None,
        error: BybitApiError | None = None,
    ) -> None:
        self._queues = {k: list(v) for k, v in (responses_by_symbol or {}).items()}
        self._error = error
        self.calls: list[dict] = []

    async def get_kline(self, category, symbol, interval, *, start=None, end=None, limit=1000):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "limit": limit})
        if self._error is not None:
            raise self._error
        queue = self._queues.get(symbol, [])
        if queue:
            return queue.pop(0)
        return {"category": category, "symbol": symbol, "list": []}


class _FakeMarketUniverse:
    def __init__(
        self, instruments: list[DomainInstrument] | None = None, unavailable: bool = False
    ):
        self._instruments = instruments or []
        self._unavailable = unavailable

    async def discover_universe(self) -> list[DomainInstrument]:
        if self._unavailable:
            raise MarketUniverseUnavailable("bybit is down")
        return self._instruments


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_instrument(
    session_factory,
    *,
    symbol: str = "BTCUSDT",
    target_start: datetime,
    synced_from: datetime,
    synced_through: datetime,
) -> int:
    async with session_factory() as session:
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol.removesuffix("USDT"),
            quote_coin="USDT",
            is_active=True,
            first_seen_at=synced_from,
            last_seen_at=synced_from,
            history_target_start=target_start,
            history_synced_from=synced_from,
            history_synced_through=synced_through,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


def _reconciler(session_factory, bybit_client, **overrides) -> HistoryReconciler:
    kwargs = {
        "retention_days": RETENTION_DAYS,
        "page_size": 1000,
        "max_concurrent_instruments": 5,
        "request_delay_seconds": 0,
        "catchup_buffer_minutes": 2,
        "universe_refresh_interval_seconds": 900,
    }
    kwargs.update(overrides)
    return HistoryReconciler(session_factory, bybit_client, _FakeMarketUniverse(), **kwargs)


# -- bootstrap -----------------------------------------------------------------


async def test_bootstrap_step_completes_in_one_pass_when_bybit_covers_the_whole_range(
    session_factory,
):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    target_start = now - timedelta(minutes=5)
    instrument_id = await _seed_instrument(
        session_factory, target_start=target_start, synced_from=now, synced_through=now
    )

    rows = [_raw_row(target_start + timedelta(minutes=i)) for i in range(5)]
    client = _FakeBybitClient({"BTCUSDT": [{"list": rows}]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        from app.services.candle_ingestion import CandleIngestionService

        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == target_start  # bootstrap complete

    assert len(await _stored_1m(session_factory, instrument_id)) == 5


async def test_bootstrap_reaches_natural_floor_on_empty_page(session_factory):
    """A newly listed instrument: Bybit has nothing before its listing time,
    so an empty page must be treated as the true start of history, not a
    failure — and never manufactures candles to fill it (M3 invariant D)."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    target_start = now - timedelta(days=200)  # further back than the symbol's real listing
    instrument_id = await _seed_instrument(
        session_factory, target_start=target_start, synced_from=now, synced_through=now
    )

    client = _FakeBybitClient({"BTCUSDT": [{"list": []}]})
    reconciler = _reconciler(session_factory, client)

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        from app.services.candle_ingestion import CandleIngestionService

        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == target_start  # accepted as the floor
    assert await _stored_1m(session_factory, instrument_id) == []  # nothing fabricated


async def test_bootstrap_resumes_incrementally_without_redownloading_covered_range(
    session_factory,
):
    """A short page (progress, not yet complete) must advance the frontier
    to exactly where real data stopped, and a follow-up step must request
    only what's still missing — proving resumability doesn't re-fetch
    already-covered history."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    target_start = now - timedelta(minutes=20)
    instrument_id = await _seed_instrument(
        session_factory, target_start=target_start, synced_from=now, synced_through=now
    )

    # First page only covers the most recent 5 of the 20 target minutes.
    first_page_start = now - timedelta(minutes=5)
    first_page = [_raw_row(first_page_start + timedelta(minutes=i)) for i in range(5)]
    client = _FakeBybitClient({"BTCUSDT": [{"list": first_page}]})
    reconciler = _reconciler(session_factory, client)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == first_page_start  # progress, not complete
        assert refreshed.history_synced_from > target_start

    # The next step's request must end just before the already-covered range.
    first_call_end_ms = client.calls[0]["end"]
    assert first_call_end_ms < int(now.timestamp() * 1000)

    second_page_start = target_start
    second_page = [_raw_row(second_page_start + timedelta(minutes=i)) for i in range(15)]
    client._queues["BTCUSDT"] = [{"list": second_page}]

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    second_call_end_ms = client.calls[1]["end"]
    assert second_call_end_ms < first_call_end_ms  # walked strictly further back

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == target_start  # now complete


async def test_bootstrap_already_complete_makes_no_rest_call(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    instrument_id = await _seed_instrument(
        session_factory, target_start=now, synced_from=now, synced_through=now
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    assert client.calls == []


# -- bootstrap respects a lowered retention horizon (retention reduction) -------


async def test_bootstrap_never_requests_history_earlier_than_the_configured_horizon(
    session_factory,
):
    """A pre-existing instrument's own `history_target_start` may still
    reflect an older, larger retention policy (e.g. 365 days, persisted
    before the operator lowered `market_history_retention_days` to 30) —
    bootstrap must never request/dig earlier than the *current* configured
    horizon, no matter what that stale per-instrument watermark says."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    stale_target = now - timedelta(days=365)  # set back when retention was 365 days
    synced_from = now - timedelta(days=25)  # partway through the current 30-day window
    instrument_id = await _seed_instrument(
        session_factory, target_start=stale_target, synced_from=synced_from, synced_through=now
    )

    rows = [_raw_row(now - timedelta(days=29) + timedelta(minutes=i)) for i in range(5)]
    client = _FakeBybitClient({"BTCUSDT": [{"list": rows}]})
    reconciler = _reconciler(session_factory, client, retention_days=30)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    # The requested start must sit right around "30 days ago" — nowhere
    # near the stale 365-day target (which would fail the lower bound by
    # over 300 days).
    lower_bound_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    upper_bound_ms = int((datetime.now(UTC) - timedelta(days=30)).timestamp() * 1000)
    assert lower_bound_ms <= client.calls[0]["start"] <= upper_bound_ms


async def test_bootstrap_already_synced_past_the_current_horizon_makes_no_rest_call(
    session_factory,
):
    """An instrument already bootstrapped deep into history under an
    older, larger retention policy (its `history_synced_from` far older
    than the current 30-day horizon) must not trigger any further
    backward bootstrap work just because its own `history_target_start`
    still says 365 days — the effective floor caps how far back is
    'needed' under the current policy, and this instrument already
    exceeds it."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    stale_target = now - timedelta(days=365)
    deep_synced_from = now - timedelta(days=200)  # already synced far past the 30-day horizon
    instrument_id = await _seed_instrument(
        session_factory,
        target_start=stale_target,
        synced_from=deep_synced_from,
        synced_through=now,
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, retention_days=30)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    assert client.calls == []
    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == deep_synced_from  # untouched


async def test_bootstrap_reaching_the_floor_snaps_to_the_effective_horizon_not_the_stale_target(
    session_factory,
):
    """When bootstrap reaches the natural floor of available history
    (nothing left for Bybit to return), the frontier must snap to the
    *effective* (current-policy) target start, not the instrument's
    stale, deeper persisted `history_target_start` — otherwise a lowered
    `retention_days` would leave a watermark claiming ~365 days of
    reconciled history when the current policy only ever asked for ~30.
    `history_target_start` itself must stay untouched — it is never
    rewritten, truthfully recording what bootstrap was originally aimed
    at."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    stale_target = now - timedelta(days=365)
    synced_from = now - timedelta(days=25)
    instrument_id = await _seed_instrument(
        session_factory, target_start=stale_target, synced_from=synced_from, synced_through=now
    )
    client = _FakeBybitClient({"BTCUSDT": [{"list": []}]})  # nothing left -> natural floor
    reconciler = _reconciler(session_factory, client, retention_days=30)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from > now - timedelta(days=31)
        assert refreshed.history_target_start == stale_target  # never rewritten


async def test_gap_recovery_inside_the_retained_window_is_unaffected_by_the_horizon(
    session_factory,
):
    """Catch-up/gap-repair (`_catchup_step`) walks `history_synced_through`
    forward within the retained window — it must keep working regardless of the configured
    retention horizon. Exercised here explicitly at the current 30-day
    policy value, mirroring
    `test_catchup_falls_back_to_rest_when_data_is_actually_missing`."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    synced_through = now - timedelta(minutes=10)
    instrument_id = await _seed_instrument(
        session_factory,
        target_start=synced_through,
        synced_from=synced_through,
        synced_through=synced_through,
    )

    gap_rows = [_raw_row(synced_through + timedelta(minutes=i + 1)) for i in range(5)]
    client = _FakeBybitClient({"BTCUSDT": [{"list": gap_rows}]})
    reconciler = _reconciler(session_factory, client, retention_days=30, catchup_buffer_minutes=2)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        candle_repo = CandleRepository(session)
        await reconciler._catchup_step(
            session, candle_repo, instrument, CandleIngestionService(candle_repo)
        )

    assert len(client.calls) == 1
    assert len(await _stored_1m(session_factory, instrument_id)) == 5


# -- catch-up / gap repair -------------------------------------------------------


async def test_catchup_uses_cheap_path_when_live_ws_already_filled_the_gap(session_factory):
    """If the live collector has already persisted the candles, catch-up
    must advance the frontier without ever calling Bybit REST."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    synced_through = now - timedelta(minutes=5)
    instrument_id = await _seed_instrument(
        session_factory,
        target_start=synced_through,
        synced_from=synced_through,
        synced_through=synced_through,
    )

    async with session_factory() as session:
        repo = CandleRepository(session)
        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": "1m",
                "open_time": synced_through + timedelta(minutes=i),
                "close_time": synced_through + timedelta(minutes=i + 1) - timedelta(microseconds=1),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "turnover": 100,
            }
            for i in range(3)  # covers up to now - 2min, i.e. the catchup buffer edge
        ]
        await repo.upsert_many(rows)

    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, catchup_buffer_minutes=2)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        candle_repo = CandleRepository(session)
        await reconciler._catchup_step(
            session, candle_repo, instrument, CandleIngestionService(candle_repo)
        )

    assert client.calls == []  # never touched Bybit
    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_through > synced_through


async def test_catchup_falls_back_to_rest_when_data_is_actually_missing(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    synced_through = now - timedelta(minutes=10)
    instrument_id = await _seed_instrument(
        session_factory,
        target_start=synced_through,
        synced_from=synced_through,
        synced_through=synced_through,
    )

    gap_rows = [_raw_row(synced_through + timedelta(minutes=i + 1)) for i in range(5)]
    client = _FakeBybitClient({"BTCUSDT": [{"list": gap_rows}]})
    reconciler = _reconciler(session_factory, client, catchup_buffer_minutes=2)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        candle_repo = CandleRepository(session)
        await reconciler._catchup_step(
            session, candle_repo, instrument, CandleIngestionService(candle_repo)
        )

    assert len(client.calls) == 1  # had to ask Bybit for the missing range
    assert len(await _stored_1m(session_factory, instrument_id)) == 5


async def test_catchup_does_not_advance_past_the_buffer_near_now(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    instrument_id = await _seed_instrument(
        session_factory, target_start=now, synced_from=now, synced_through=now
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, catchup_buffer_minutes=2)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        candle_repo = CandleRepository(session)
        await reconciler._catchup_step(
            session, candle_repo, instrument, CandleIngestionService(candle_repo)
        )

    assert client.calls == []
    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_through == now  # nothing to do yet


# -- live/bootstrap overlap is idempotent ---------------------------------------


async def test_bootstrap_ingestion_overlapping_a_live_candle_is_idempotent(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    target_start = now - timedelta(minutes=1)
    instrument_id = await _seed_instrument(
        session_factory, target_start=target_start, synced_from=now, synced_through=now
    )

    # The live WebSocket collector already persisted this exact minute.
    async with session_factory() as session:
        repo = CandleRepository(session)
        await repo.upsert_many(
            [
                {
                    "instrument_id": instrument_id,
                    "timeframe": "1m",
                    "open_time": target_start,
                    "close_time": target_start + timedelta(minutes=1) - timedelta(microseconds=1),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1,
                    "turnover": 100,
                }
            ]
        )

    # Bootstrap independently fetches the same minute via REST.
    client = _FakeBybitClient({"BTCUSDT": [{"list": [_raw_row(target_start)]}]})
    reconciler = _reconciler(session_factory, client)

    from app.services.candle_ingestion import CandleIngestionService

    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        await reconciler._bootstrap_step(
            session, instrument, CandleIngestionService(CandleRepository(session))
        )

    assert len(await _stored_1m(session_factory, instrument_id)) == 1  # no duplicate row


# -- recovery failure must not crash ---------------------------------------------


async def test_process_instrument_survives_a_bybit_failure(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    target_start = now - timedelta(minutes=5)
    instrument_id = await _seed_instrument(
        session_factory, target_start=target_start, synced_from=now, synced_through=now
    )
    client = _FakeBybitClient(error=BybitApiError("boom"))
    reconciler = _reconciler(session_factory, client)
    reconciler._stop_event = asyncio.Event()

    await reconciler._process_instrument(instrument_id, asyncio.Semaphore(1))  # must not raise

    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        assert refreshed.history_synced_from == now  # unchanged, not corrupted


async def test_process_instrument_survives_an_unexpected_exception(session_factory):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    instrument_id = await _seed_instrument(
        session_factory,
        target_start=now - timedelta(minutes=1),
        synced_from=now,
        synced_through=now,
    )

    class _ExplodingClient(_FakeBybitClient):
        async def get_kline(self, *args, **kwargs):
            raise RuntimeError("unexpected")

    reconciler = _reconciler(session_factory, _ExplodingClient())
    reconciler._stop_event = asyncio.Event()

    await reconciler._process_instrument(instrument_id, asyncio.Semaphore(1))  # must not raise


# -- universe reconciliation ------------------------------------------------------


async def test_universe_reconciliation_notifies_hook_of_new_symbols_only(session_factory):
    seen: list[list[str]] = []

    async def on_new_symbols(symbols: list[str]) -> None:
        seen.append(symbols)

    market_universe = _FakeMarketUniverse(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")]
    )
    reconciler = HistoryReconciler(
        session_factory,
        _FakeBybitClient(),
        market_universe,
        retention_days=RETENTION_DAYS,
        on_new_symbols=on_new_symbols,
    )

    await reconciler._reconcile_universe_once()
    assert seen == [["BTCUSDT"]]

    await reconciler._reconcile_universe_once()  # same symbol again: nothing new
    assert seen == [["BTCUSDT"]]


async def test_universe_reconciliation_survives_bybit_being_unavailable(session_factory):
    hook_calls = []
    reconciler = HistoryReconciler(
        session_factory,
        _FakeBybitClient(),
        _FakeMarketUniverse(unavailable=True),
        retention_days=RETENTION_DAYS,
        on_new_symbols=lambda symbols: hook_calls.append(symbols),
    )

    await reconciler._reconcile_universe_once()  # must not raise

    assert hook_calls == []
    async with session_factory() as session:
        assert (await InstrumentRepository(session).list_active()) == []


# -- lifecycle --------------------------------------------------------------------


async def test_start_and_stop_is_clean_with_an_empty_universe(session_factory):
    reconciler = HistoryReconciler(
        session_factory,
        _FakeBybitClient(),
        _FakeMarketUniverse(),
        retention_days=RETENTION_DAYS,
        universe_refresh_interval_seconds=60,
    )

    await reconciler.start()
    assert reconciler.running is True

    await asyncio.wait_for(reconciler.stop(), timeout=2)
    assert reconciler.running is False


async def test_long_outage_catchup_skips_expired_history_without_rewriting_past(session_factory):
    from app.services.candle_ingestion import CandleIngestionService

    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    instrument_id = await _seed_instrument(
        session_factory, target_start=old, synced_from=old, synced_through=old
    )
    client = _FakeBybitClient()
    reconciler = _reconciler(session_factory, client, retention_days=30)
    before = datetime.now(UTC) - timedelta(days=30)
    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        repo = CandleRepository(session)
        await reconciler._catchup_step(session, repo, instrument, CandleIngestionService(repo))
    after = datetime.now(UTC) - timedelta(days=30)
    assert (
        int(before.timestamp() * 1000)
        <= client.calls[0]["start"]
        <= int(after.timestamp() * 1000) + 1
    )
    async with session_factory() as session:
        instrument = await session.get(Instrument, instrument_id)
        assert instrument.history_target_start == old
        assert instrument.history_synced_from == old
        assert instrument.history_synced_through > before


async def test_retention_prunes_members_before_candles_and_rolls_partitions(
    session_factory, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.services import partition_manager

    drop = AsyncMock()
    ensure = AsyncMock()
    monkeypatch.setattr(partition_manager, "drop_expired_partitions", drop)
    monkeypatch.setattr(partition_manager, "ensure_partitions", ensure)
    reconciler = _reconciler(session_factory, _FakeBybitClient(), retention_days=30)
    await reconciler._reconcile_universe_once()
    assert [c.kwargs["table"] for c in drop.call_args_list] == [
        "market_frame_members",
        "sniffer_results",
        "market_candles",
    ]
    assert all(c.kwargs["retention_days"] == 30 for c in drop.call_args_list)
    assert ensure.await_count == 3
