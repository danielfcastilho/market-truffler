"""MARKET's continuously-running historical capability: universe
reconciliation, partition upkeep, one-year bootstrap, and gap/outage
recovery — all driven by one per-instrument watermark pair persisted on
`Instrument` (see its docstring for the exact semantics).

Like `MarketCollector`, this is started once from the FastAPI lifespan and
runs for the life of the process. Vitals only ever reads what it has
already persisted; nothing here is triggered by a request.

Bootstrap and gap-repair are the same mechanism applied to different parts
of the timeline: `_bootstrap_step` walks `history_synced_from` backward
toward `history_target_start`; `_catchup_step` walks `history_synced_through`
forward toward "now". Both proceed one bounded REST page at a time per
instrument per round, round-robin across the whole universe with bounded
concurrency, so no single instrument (and no single round) does unbounded
work.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
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

SessionFactory = Callable[[], AsyncSession]
NewSymbolsHook = Callable[[list[str]], "Awaitable[None]"]


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
        self._on_new_symbols = on_new_symbols

        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._stop_event = asyncio.Event()

        months_back = (self._retention_days // 30) + 2
        async with self._session_factory() as session:
            await partition_manager.ensure_partitions(session, months_back=months_back)

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
            return

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            active, newly_added = await InstrumentRepository(session).reconcile_universe(
                discovered, now=now, retention_days=self._retention_days
            )
            await partition_manager.drop_expired_partitions(
                session, retention_days=self._retention_days
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
        if instrument.history_synced_from <= instrument.history_target_start:
            return  # bootstrap already complete

        end_ms = int(instrument.history_synced_from.timestamp() * 1000) - 1
        start_ms = int(instrument.history_target_start.timestamp() * 1000)
        if end_ms < start_ms:
            instrument.history_synced_from = instrument.history_target_start
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
            instrument.history_synced_from = instrument.history_target_start
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

        target_end = datetime.now(UTC) - self._catchup_buffer
        if instrument.history_synced_through >= target_end:
            return

        max_chunk = instrument.history_synced_through + timedelta(minutes=self._page_size)
        chunk_end = min(target_end, max_chunk)
        if chunk_end <= instrument.history_synced_through:
            return

        # Cheap path: the live WebSocket collector may already have filled
        # this chunk in real time. Only fall back to a REST call for
        # whatever's genuinely missing.
        span_seconds = (chunk_end - instrument.history_synced_through).total_seconds()
        expected_minutes = int(span_seconds // 60)
        existing = await candle_repo.fetch_range(
            instrument.id, "1m", instrument.history_synced_through, chunk_end
        )
        if len(existing) >= expected_minutes:
            instrument.history_synced_through = chunk_end
            await session.commit()
            return

        start_ms = int(instrument.history_synced_through.timestamp() * 1000) + 1
        end_ms = int(chunk_end.timestamp() * 1000)
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
                "history_catchup_page_failed",
                extra={"symbol": instrument.symbol, "error": str(exc)},
            )
            return

        rows = raw.get("list", [])
        if rows:
            candles = [
                to_closed_candle(instrument.symbol, parse_kline_history_row(row)) for row in rows
            ]
            await candle_ingestion.ingest_1m_candles(instrument.id, candles)

        instrument.history_synced_through = chunk_end
        await session.commit()
