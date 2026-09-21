"""MARKET's continuously-running historical capability: universe
reconciliation, partition upkeep, rolling-horizon bootstrap, and
gap/outage recovery — all driven by one per-instrument watermark pair
persisted on `Instrument` (see its docstring for the exact semantics) and
one rolling window, `settings.market_history_retention_days` (currently
~30 days; see that setting's docstring for the current value and how to
change it).

Like `MarketCollector`, this is started once from the FastAPI lifespan and
runs for the life of the process. Vitals only ever reads what it has
already persisted; nothing here is triggered by a request.

Bootstrap and gap-repair are the same mechanism applied to different parts
of the timeline: `_bootstrap_step` walks `history_synced_from` backward
toward the *effective* target start (the later of the instrument's own
persisted `history_target_start` and `now - retention_days` — see
`_bootstrap_step`'s own docstring/comments for why it's never just the
persisted value); `_catchup_step` walks `history_synced_through` forward
toward "now". Both proceed one bounded REST page at a time per instrument
per round, round-robin across the whole universe with bounded concurrency,
so no single instrument (and no single round) does unbounded work.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.bybit.client import BybitApiError, BybitClient
from app.integrations.bybit.schemas import parse_kline_history_row
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.instrument_repository import InstrumentRepository
from app.services import partition_manager
from app.services.candle_conversion import to_closed_candle
from app.services.candle_ingestion import CandleIngestionService
from app.services.market_universe import MarketUniverseUnavailable

logger = logging.getLogger(__name__)

_CATEGORY = "linear"

# Every RANGE-partitioned, per-minute table MARKET/Sniffer own, sharing the
# same rolling retention horizon and monthly partition scheme. Partitioned
# by open_time; the frame/Sniffer tables are partitioned by frame_time, but
# `partition_manager` only needs the column name for documentation — the
# DDL itself is generic.
_PARTITIONED_TABLES: tuple[tuple[str, str], ...] = (
    ("market_frame_members", "frame_time"),
    ("sniffer_results", "frame_time"),
    ("market_candles", "open_time"),
)

SessionFactory = Callable[[], AsyncSession]
NewSymbolsHook = Callable[[list[str]], "Awaitable[None]"]


@dataclass
class HistoryReconcilerStatus:
    """Read-only snapshot of the reconciler's most recent live Bybit REST
    contact — from its own already-scheduled universe-refresh work, never a
    fresh call made on Vitals' behalf. `last_universe_refresh_ok=None`
    means "hasn't run yet" (only possible before `start()`'s initial
    synchronous refresh completes); Vitals treats that the same as "down"
    since it can't yet claim reachability either way."""

    last_universe_refresh_at: datetime | None = None
    last_universe_refresh_ok: bool | None = None


class HistoryReconciler:
    def __init__(
        self,
        session_factory: SessionFactory,
        bybit_client: BybitClient,
        market_universe: Any,  # MarketUniverseService, duck-typed for testability
        *,
        retention_days: int,
        page_size: int = 1000,
        max_concurrent_instruments: int = 5,
        request_delay_seconds: float = 0.2,
        catchup_buffer_minutes: int = 2,
        universe_refresh_interval_seconds: float = 900.0,
        gap_retry_delay_seconds: float = 3.0,
        on_new_symbols: NewSymbolsHook | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bybit_client = bybit_client
        self._market_universe = market_universe
        self._retention_days = retention_days
        self._page_size = page_size
        self._max_concurrent = max_concurrent_instruments
        self._request_delay = request_delay_seconds
        self._catchup_buffer = timedelta(minutes=catchup_buffer_minutes)
        self._universe_refresh_interval = universe_refresh_interval_seconds
        self._gap_retry_delay = gap_retry_delay_seconds
        self._on_new_symbols = on_new_symbols

        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._status = HistoryReconcilerStatus()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def status(self) -> HistoryReconcilerStatus:
        return self._status

    async def start(self) -> None:
        self._stop_event = asyncio.Event()

        months_back = (self._retention_days // 30) + 2
        async with self._session_factory() as session:
            for table, column in _PARTITIONED_TABLES:
                await partition_manager.ensure_partitions(
                    session, months_back=months_back, table=table, partition_column=column
                )

        await self._reconcile_universe_once()

        self._tasks = [
            asyncio.create_task(self._run_universe_refresh_loop()),
            asyncio.create_task(self._run_reconciliation_loop()),
        ]
        self._running = True
        logger.info("history_reconciler_started")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        self._running = False
        logger.info("history_reconciler_stopped")

    # -- universe reconciliation -------------------------------------------------

    async def _run_universe_refresh_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            if await self._wait_or_stop(self._universe_refresh_interval):
                break
            await self._reconcile_universe_once()

    async def _reconcile_universe_once(self) -> None:
        try:
            discovered = await self._market_universe.discover_universe()
        except MarketUniverseUnavailable:
            logger.warning("history_reconciler_universe_refresh_failed")
            self._status.last_universe_refresh_at = datetime.now(UTC)
            self._status.last_universe_refresh_ok = False
            return

        self._status.last_universe_refresh_at = datetime.now(UTC)
        self._status.last_universe_refresh_ok = True

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            active, newly_added = await InstrumentRepository(session).reconcile_universe(
                discovered, now=now, retention_days=self._retention_days
            )
            for table, column in _PARTITIONED_TABLES:
                await partition_manager.ensure_partitions(
                    session, months_back=0, table=table, partition_column=column
                )
                await partition_manager.drop_expired_partitions(
                    session, retention_days=self._retention_days, table=table
                )

        logger.info(
            "universe_reconciled",
            extra={"active_instruments": len(active), "newly_added": len(newly_added)},
        )
        if newly_added and self._on_new_symbols is not None:
            await self._on_new_symbols(newly_added)

    # -- bootstrap / catch-up round-robin -----------------------------------------

    async def _run_reconciliation_loop(self) -> None:
        assert self._stop_event is not None
        semaphore = asyncio.Semaphore(self._max_concurrent)
        while not self._stop_event.is_set():
            async with self._session_factory() as session:
                instrument_ids = [i.id for i in await InstrumentRepository(session).list_active()]

            if not instrument_ids:
                if await self._wait_or_stop(5.0):
                    break
                continue

            await asyncio.gather(
                *(
                    self._process_instrument(instrument_id, semaphore)
                    for instrument_id in instrument_ids
                )
            )
            if await self._wait_or_stop(self._request_delay):
                break

    async def _wait_or_stop(self, seconds: float) -> bool:
        """Sleep for `seconds`, but wake early (returning True) if stopped."""
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _process_instrument(self, instrument_id: int, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                async with self._session_factory() as session:
                    instrument = await session.get(Instrument, instrument_id)
                    if instrument is None or not instrument.is_active:
                        return
                    candle_repo = CandleRepository(session)
                    candle_ingestion = CandleIngestionService(candle_repo)

                    await self._bootstrap_step(session, instrument, candle_ingestion)
                    await self._catchup_step(session, candle_repo, instrument, candle_ingestion)
            except Exception:  # noqa: BLE001 - one bad symbol must not stop the reconciler
                logger.exception(
                    "history_reconciliation_step_failed", extra={"instrument_id": instrument_id}
                )

    # -- backward bootstrap: history_synced_from -> history_target_start --------

    async def _bootstrap_step(
        self,
        session: AsyncSession,
        instrument: Instrument,
        candle_ingestion: CandleIngestionService,
    ) -> None:
        if instrument.history_synced_from is None or instrument.history_target_start is None:
            return

        # `instrument.history_target_start` is an honest, never-rewritten
        # record of what bootstrap was aimed at when this instrument was
        # first discovered. If the operator has since lowered
        # `market_history_retention_days` (e.g. 365 -> 30), that persisted
        # value may now point deeper than the current policy promises. The
        # *effective* floor bootstrap actually walks toward is always
        # bounded by the current retention window too — same pattern as
        # `historical_coverage.compute_historical_coverage` — so a stale
        # watermark can never make the reconciler dig toward history that
        # today's policy no longer retains.
        effective_target_start = max(
            instrument.history_target_start,
            datetime.now(UTC) - timedelta(days=self._retention_days),
        )

        if instrument.history_synced_from <= effective_target_start:
            return  # bootstrap already complete under the current policy

        end_ms = int(instrument.history_synced_from.timestamp() * 1000) - 1
        start_ms = int(effective_target_start.timestamp() * 1000)
        if end_ms < start_ms:
            instrument.history_synced_from = effective_target_start
            await session.commit()
            return

        try:
            raw = await self._bybit_client.get_kline(
                category=_CATEGORY,
                symbol=instrument.symbol,
                interval="1",
                start=start_ms,
                end=end_ms,
                limit=self._page_size,
            )
        except BybitApiError as exc:
            logger.warning(
                "history_bootstrap_page_failed",
                extra={"symbol": instrument.symbol, "error": str(exc)},
            )
            return

        rows = raw.get("list", [])
        if not rows:
            # Nothing left in [target_start, frontier): we've reached the
            # natural floor of available history (a new listing, or the
            # exchange's own retention limit) — never manufacture data to
            # fill it, just accept it as the true start of history.
            instrument.history_synced_from = effective_target_start
            await session.commit()
            logger.info("history_bootstrap_reached_floor", extra={"symbol": instrument.symbol})
            return

        parsed = [parse_kline_history_row(row) for row in rows]
        candles = [to_closed_candle(instrument.symbol, kline) for kline in parsed]
        await candle_ingestion.ingest_1m_candles(instrument.id, candles)

        oldest_ms = min(kline.start for kline in parsed)
        instrument.history_synced_from = datetime.fromtimestamp(oldest_ms / 1000, tz=UTC)
        await session.commit()

    # -- forward catch-up: history_synced_through -> now - buffer ----------------

    async def _catchup_step(
        self,
        session: AsyncSession,
        candle_repo: CandleRepository,
        instrument: Instrument,
        candle_ingestion: CandleIngestionService,
    ) -> None:
        if instrument.history_synced_through is None:
            return

        # Resolved once, up front: `candle_ingestion.ingest_1m_candles`
        # (called below, possibly twice) upserts via Core and then calls
        # `session.expire_all()` (see `CandleRepository.upsert_many`), so
        # `instrument`'s attributes become lazy-load-on-access afterwards
        # — and a lazy reload from inside an already-running async call
        # raises `MissingGreenlet`. Capturing the plain values we need
        # before any ingestion happens avoids ever touching a
        # possibly-expired attribute again in this method (the final
        # assignment to `history_synced_through` is a pure write, which
        # SQLAlchemy never needs to reload for).
        instrument_id = instrument.id
        symbol = instrument.symbol

        now = datetime.now(UTC)
        target_end = now - self._catchup_buffer
        # Skip expired work after a long outage without rewriting the historical
        # backward frontier. Coverage already intersects it with this window.
        catchup_start = max(
            instrument.history_synced_through, now - timedelta(days=self._retention_days)
        )
        if instrument.history_synced_through >= target_end:
            return

        max_chunk = catchup_start + timedelta(minutes=self._page_size)
        chunk_end = min(target_end, max_chunk)
        if chunk_end <= catchup_start:
            return

        # Cheap path: the live WebSocket collector may already have filled
        # this chunk in real time. Only fall back to a REST call for
        # whatever's genuinely missing.
        if await self._chunk_complete(candle_repo, instrument_id, catchup_start, chunk_end):
            instrument.history_synced_through = chunk_end
            await session.commit()
            return

        fetched = await self._fetch_and_ingest_page(
            instrument_id, symbol, candle_ingestion, catchup_start, chunk_end
        )
        if not fetched:
            return  # request itself failed; retry this exact chunk next round

        # A gap this close to "now" can be the exchange's REST kline
        # endpoint simply not having indexed the very latest minute(s) yet
        # — it can lag a little behind what the WebSocket already
        # delivered live, independent of `catchup_buffer_minutes`. Give it
        # exactly one bounded retry before accepting whatever's left as a
        # genuine, permanent gap (no trade recorded that minute) — never
        # more than one retry, so a truly-absent candle can't stall this
        # instrument's catch-up forever. Without this, a single
        # transiently-incomplete REST response used to get the watermark
        # advanced past the gap anyway, permanently sealing in a hole that
        # was actually recoverable (this is what caused every instrument
        # to simultaneously lose one live minute and never recover it,
        # breaking every RSI window that minute fell inside).
        if not await self._chunk_complete(candle_repo, instrument_id, catchup_start, chunk_end):
            await asyncio.sleep(self._gap_retry_delay)
            await self._fetch_and_ingest_page(
                instrument_id, symbol, candle_ingestion, catchup_start, chunk_end
            )

        instrument.history_synced_through = chunk_end
        await session.commit()

    async def _chunk_complete(
        self,
        candle_repo: CandleRepository,
        instrument_id: int,
        start: datetime,
        end: datetime,
    ) -> bool:
        """Whether every expected 1m minute in the half-open `[start, end)`
        window is already present — a plain count comparison is exact
        here since `(instrument_id, timeframe, open_time)` is the
        candle's primary key, so `fetch_range` can never return a
        duplicate open_time to inflate the count."""
        expected_minutes = int((end - start).total_seconds() // 60)
        existing = await candle_repo.fetch_range(instrument_id, "1m", start, end)
        return len(existing) >= expected_minutes

    async def _fetch_and_ingest_page(
        self,
        instrument_id: int,
        symbol: str,
        candle_ingestion: CandleIngestionService,
        start: datetime,
        end: datetime,
    ) -> bool:
        """Fetch and ingest one REST page for `[start, end)`. Returns
        whether the request itself succeeded — a successful request that
        came back short of the full expected window is still `True`; it's
        the caller's job to decide whether a remaining gap is acceptable."""
        start_ms = int(start.timestamp() * 1000) + 1
        end_ms = int(end.timestamp() * 1000)
        try:
            raw = await self._bybit_client.get_kline(
                category=_CATEGORY,
                symbol=symbol,
                interval="1",
                start=start_ms,
                end=end_ms,
                limit=self._page_size,
            )
        except BybitApiError as exc:
            logger.warning(
                "history_catchup_page_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return False

        rows = raw.get("list", [])
        if rows:
            candles = [to_closed_candle(symbol, parse_kline_history_row(row)) for row in rows]
            await candle_ingestion.ingest_1m_candles(instrument_id, candles)
        return True
