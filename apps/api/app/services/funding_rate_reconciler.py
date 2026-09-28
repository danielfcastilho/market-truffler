"""MARKET's continuously-running Funding Rate capability: bootstrap +
forward catch-up over Bybit's public REST `/v5/market/funding/history`
endpoint, one bounded page per instrument per round — the same shape
`OpenInterestReconciler` uses, simplified for the same reason OI's is:
funding only needs a short rolling window (`REQUIRED_WARMUP`-scale, not
the ~30-day candle retention) and has no WebSocket source of its own.

Unlike Open Interest, this endpoint has no fixed bucket granularity to
request — each returned entry is an actual settled funding event at
whatever interval that instrument's contract really uses (commonly
1h/2h/4h/8h, and not necessarily constant over an instrument's lifetime).
MARKET never assumes or hardcodes an interval per symbol: it just walks
Bybit's own reported settlement timestamps, exactly as they come back.

Like `OpenInterestReconciler`, this is started once from the FastAPI
lifespan and runs for the life of the process; nothing here is triggered
by a request.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.bybit.client import BybitApiError, BybitClient
from app.integrations.bybit.schemas import FundingRateHistoryResult
from app.models.instrument import Instrument
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.instrument_repository import InstrumentRepository

logger = logging.getLogger(__name__)

_CATEGORY = "linear"
_PAGE_SIZE = 200  # Bybit's own cap for this endpoint

SessionFactory = Callable[[], AsyncSession]


@dataclass
class FundingRateReconcilerStatus:
    """Read-only snapshot of the reconciler's most recent poll round."""

    last_poll_at: datetime | None = None
    last_poll_ok: bool | None = None


def _to_rows(instrument_id: int, result: FundingRateHistoryResult) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": instrument_id,
            "funding_time": datetime.fromtimestamp(int(entry.fundingRateTimestamp) / 1000, tz=UTC),
            "funding_rate": Decimal(entry.fundingRate),
        }
        for entry in result.list
    ]


class FundingRateReconciler:
    def __init__(
        self,
        session_factory: SessionFactory,
        bybit_client: BybitClient,
        *,
        required_warmup: timedelta,
        retention: timedelta | None = None,
        poll_interval_seconds: float = 300.0,
        max_concurrent_instruments: int = 5,
        request_delay_seconds: float = 0.2,
    ) -> None:
        self._session_factory = session_factory
        self._bybit_client = bybit_client
        self._required_warmup = required_warmup
        # Same invariant as OpenInterestReconciler's own `_retention`: must
        # never be shorter than required_warmup, or every poll round would
        # prune bootstrap progress it just walked back to. Defaults to
        # required_warmup plus a one-day buffer, kept automatically
        # consistent as REQUIRED_WARMUP grows (see
        # app.features.engine.REQUIRED_WARMUP); an explicit override is
        # still honored (e.g. tests).
        self._retention = (
            retention if retention is not None else required_warmup + timedelta(days=1)
        )
        self._poll_interval = poll_interval_seconds
        self._max_concurrent = max_concurrent_instruments
        self._request_delay = request_delay_seconds

        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._status = FundingRateReconcilerStatus()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def status(self) -> FundingRateReconcilerStatus:
        return self._status

    async def start(self) -> None:
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        self._running = True
        logger.info("funding_rate_reconciler_started")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._running = False
        logger.info("funding_rate_reconciler_stopped")

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            await self._poll_once()
            if await self._wait_or_stop(self._poll_interval):
                break

    async def _wait_or_stop(self, seconds: float) -> bool:
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _poll_once(self) -> None:
        async with self._session_factory() as session:
            instrument_ids = [i.id for i in await InstrumentRepository(session).list_active()]

        semaphore = asyncio.Semaphore(self._max_concurrent)
        await asyncio.gather(
            *(
                self._process_instrument(instrument_id, semaphore)
                for instrument_id in instrument_ids
            )
        )

        async with self._session_factory() as session:
            await FundingRateRepository(session).delete_before(
                datetime.now(UTC) - self._retention
            )

        self._status.last_poll_at = datetime.now(UTC)
        self._status.last_poll_ok = True

    async def _process_instrument(self, instrument_id: int, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                async with self._session_factory() as session:
                    instrument = await session.get(Instrument, instrument_id)
                    if instrument is None or not instrument.is_active:
                        return
                    symbol = instrument.symbol
                    repo = FundingRateRepository(session)

                    await self._bootstrap_step(session, repo, instrument, symbol)
                    await self._catchup_step(session, repo, instrument, symbol)
            except Exception:  # noqa: BLE001 - one bad symbol must not stop the round
                logger.exception(
                    "funding_rate_reconciliation_step_failed",
                    extra={"instrument_id": instrument_id},
                )
            await asyncio.sleep(self._request_delay)

    # -- backward bootstrap: funding_synced_from -> now - required_warmup ------

    async def _bootstrap_step(
        self,
        session: AsyncSession,
        repo: FundingRateRepository,
        instrument: Instrument,
        symbol: str,
    ) -> None:
        now = datetime.now(UTC)
        target_start = now - self._required_warmup
        synced_from = instrument.funding_synced_from

        if synced_from is not None and synced_from <= target_start:
            return  # already bootstrapped far enough back

        # One bounded page per round, walking backward from the current
        # frontier — same shape as OpenInterestReconciler._bootstrap_step.
        end = synced_from if synced_from is not None else now
        start = target_start
        if end <= start:
            instrument.funding_synced_from = target_start
            await session.commit()
            return

        try:
            raw = await self._bybit_client.get_funding_rate_history(
                category=_CATEGORY,
                symbol=symbol,
                start=int(start.timestamp() * 1000),
                end=int(end.timestamp() * 1000) - 1,
                limit=_PAGE_SIZE,
            )
        except BybitApiError as exc:
            logger.warning(
                "funding_rate_bootstrap_page_failed", extra={"symbol": symbol, "error": str(exc)}
            )
            return

        result = FundingRateHistoryResult.model_validate(raw)
        if not result.list:
            # Nothing left between target_start and the current frontier:
            # reached the natural floor (a new listing, or the exchange's
            # own funding history limit) — accept it as the true start,
            # never manufacture data to fill it. Same convention as
            # OpenInterestReconciler._bootstrap_step's empty-page handling.
            instrument.funding_synced_from = target_start
            await session.commit()
            return

        await repo.upsert_many(_to_rows(instrument.id, result))

        oldest_ms = min(int(entry.fundingRateTimestamp) for entry in result.list)
        instrument.funding_synced_from = datetime.fromtimestamp(oldest_ms / 1000, tz=UTC)
        await session.commit()

    # -- forward catch-up: latest persisted observation -> now ------------------

    async def _catchup_step(
        self,
        session: AsyncSession,
        repo: FundingRateRepository,
        instrument: Instrument,
        symbol: str,
    ) -> None:
        latest = await repo.fetch_latest_funding_time_per_instrument([instrument.id])
        since = latest.get(instrument.id)
        if since is None:
            return  # nothing bootstrapped yet this round — next round's bootstrap seeds it

        now = datetime.now(UTC)
        if since >= now:
            return

        try:
            raw = await self._bybit_client.get_funding_rate_history(
                category=_CATEGORY,
                symbol=symbol,
                start=int(since.timestamp() * 1000) + 1,
                end=int(now.timestamp() * 1000),
                limit=_PAGE_SIZE,
            )
        except BybitApiError as exc:
            logger.warning(
                "funding_rate_catchup_page_failed", extra={"symbol": symbol, "error": str(exc)}
            )
            return

        result = FundingRateHistoryResult.model_validate(raw)
        if not result.list:
            return  # no new settlement has occurred since `since` yet — fine, retried next round

        await repo.upsert_many(_to_rows(instrument.id, result))
