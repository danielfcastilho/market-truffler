"""MARKET's continuously-running Open Interest capability: bootstrap +
forward catch-up over Bybit's public REST `/v5/market/open-interest`
endpoint, one bounded page per instrument per round — the same shape
`HistoryReconciler` uses for candles, simplified because OI only needs a
short rolling window (`REQUIRED_WARMUP`-scale, not the ~30-day candle
retention) and has no WebSocket source of its own.

Bybit's public WebSocket does carry a live `openInterest` field on its
`tickers` topic, but the REST history endpoint already buckets OI at a
fixed 5-minute granularity — polling it once per 5-minute boundary is
exactly as fresh as that native granularity allows, so a second, WS-based live path
would add real complexity (another connection type, another reconnect/
backoff policy, another sink) for freshness this app would never actually
observe. One REST endpoint, one poll loop, covers both backfill and
"live" updates.

Rounds are aligned to the frame boundaries (`app.services.boundary_schedule`):
Bybit publishes bucket T about 10-20s after T (measured 2026-10-24: every
symbol by T+20s, never revised afterwards), so a round starts
`ROUND_OFFSET` after each boundary, and the few symbols still without
bucket T are retried once. Both finish well before frame T's decision time
(`app.domain.temporal`). Each row records `ingested_at`, the instant it was
first stored — the availability evidence Sniffer and replay filter on.

Like `MarketCollector`/`HistoryReconciler`, this is started once from the
FastAPI lifespan and runs for the life of the process; nothing here is
triggered by a request.
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
from app.integrations.bybit.schemas import OpenInterestHistoryResult
from app.models.instrument import Instrument
from app.repositories.instrument_repository import InstrumentRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.services.boundary_schedule import wait_for_round

logger = logging.getLogger(__name__)

_CATEGORY = "linear"
_INTERVAL_TIME = "5min"
_BUCKET = timedelta(minutes=5)
_PAGE_SIZE = 200  # Bybit's own cap for this endpoint
#: Forward catch-up walks the gap after the latest stored observation in
#: chronological windows of fewer buckets than one page holds, so a single
#: response normally covers a window completely (Bybit returns the
#: *newest* rows of a requested range first — a range wider than one page
#: used to return only its newest ~16.7h, after which `MAX(observed_at)`
#: jumped to "now" and permanently sealed the older part of the gap).
CATCHUP_WINDOW = _BUCKET * (_PAGE_SIZE - 1)
#: Bound on how much of a gap one instrument recovers per poll round
#: (4 x ~16.6h); a multi-day outage is recovered over a few rounds, oldest
#: first, without one round's work growing unboundedly.
MAX_CATCHUP_WINDOWS_PER_ROUND = 4
#: How long after each 5-minute boundary a round starts (bucket T is
#: published 10-20s after T).
ROUND_OFFSET = timedelta(seconds=15)
#: Pause before the single retry of symbols whose bucket T was not
#: published yet when the round reached them.
RETRY_DELAY_SECONDS = 5.0

SessionFactory = Callable[[], AsyncSession]


@dataclass
class OpenInterestReconcilerStatus:
    """Read-only snapshot of the reconciler's most recent poll round."""

    last_poll_at: datetime | None = None
    last_poll_ok: bool | None = None


def _to_rows(
    instrument_id: int, result: OpenInterestHistoryResult, ingested_at: datetime
) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": instrument_id,
            "observed_at": datetime.fromtimestamp(int(entry.timestamp) / 1000, tz=UTC),
            "open_interest": Decimal(entry.openInterest),
            "ingested_at": ingested_at,
        }
        for entry in result.list
    ]


async def fetch_open_interest_window(
    client: BybitClient, symbol: str, start: datetime, end: datetime
) -> OpenInterestHistoryResult:
    """Every 5-minute OI bucket Bybit has for `symbol` in `[start, end]`,
    following `nextPageCursor` while pages come back full — so a partial
    (truncated) response is never mistaken for complete coverage. Bounded:
    at most enough pages for the window's bucket count, plus one. Raises
    `BybitApiError` if any page fails (nothing partial is returned)."""
    max_pages = int((end - start) / _BUCKET) // _PAGE_SIZE + 2
    entries = []
    cursor: str | None = None
    category = ""
    for _ in range(max_pages):
        raw = await client.get_open_interest(
            category=_CATEGORY,
            symbol=symbol,
            interval_time=_INTERVAL_TIME,
            start=int(start.timestamp() * 1000),
            end=int(end.timestamp() * 1000),
            cursor=cursor,
            limit=_PAGE_SIZE,
        )
        page = OpenInterestHistoryResult.model_validate(raw)
        category = page.category
        entries.extend(page.list)
        cursor = page.nextPageCursor or None
        if len(page.list) < _PAGE_SIZE or cursor is None:
            break
    else:
        raise BybitApiError(f"{symbol}: more than {max_pages} OI pages for one window")
    return OpenInterestHistoryResult(category=category, symbol=symbol, list=entries)


class OpenInterestReconciler:
    def __init__(
        self,
        session_factory: SessionFactory,
        bybit_client: BybitClient,
        *,
        required_warmup: timedelta,
        retention: timedelta | None = None,
        round_offset: timedelta = ROUND_OFFSET,
        # 25 concurrent requests cover ~800 symbols in ~15s (~55 req/s,
        # well under Bybit's 600 requests / 5s per IP).
        max_concurrent_instruments: int = 25,
        request_delay_seconds: float = 0.0,
    ) -> None:
        self._session_factory = session_factory
        self._bybit_client = bybit_client
        self._required_warmup = required_warmup
        # Must never be shorter than `required_warmup`, or every poll round
        # would prune bootstrap progress it just walked back to — the
        # reconciler would perpetually re-fetch the same window and no
        # symbol could ever reach READY for OI. Defaults to
        # `required_warmup` plus a one-day buffer rather than an
        # independent constant, so it automatically stays consistent as
        # `REQUIRED_WARMUP` grows with future features (see
        # `app.features.engine.REQUIRED_WARMUP`) — an explicit override is
        # still honored (e.g. tests), but must uphold the same invariant.
        self._retention = (
            retention if retention is not None else required_warmup + timedelta(days=1)
        )
        self._round_offset = round_offset
        self._max_concurrent = max_concurrent_instruments
        self._request_delay = request_delay_seconds

        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._status = OpenInterestReconcilerStatus()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def status(self) -> OpenInterestReconcilerStatus:
        return self._status

    async def start(self) -> None:
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        self._running = True
        logger.info("open_interest_reconciler_started")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._running = False
        logger.info("open_interest_reconciler_stopped")

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        # One immediate round (bootstrap progress on startup), then one per
        # boundary.
        await self._poll_once()
        while not self._stop_event.is_set():
            boundary = await wait_for_round(self._stop_event, self._round_offset)
            if boundary is None:
                break
            await self._poll_once()
            await self._retry_missing_bucket(boundary)

    async def _retry_missing_bucket(self, boundary: datetime) -> None:
        """Retry, once, the bootstrapped symbols whose newest stored bucket
        is still older than `boundary` — normally the few Bybit published
        after the round reached them. Anything still missing simply has no
        current OI for that frame (never waited for past its decision
        time)."""
        assert self._stop_event is not None
        async with self._session_factory() as session:
            ids = [i.id for i in await InstrumentRepository(session).list_active()]
            latest = await OpenInterestRepository(session).fetch_latest_observed_at_per_instrument(
                ids
            )
        missing = [i for i in ids if i in latest and latest[i] < boundary]
        if not missing:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=RETRY_DELAY_SECONDS)
            return  # stopping
        except TimeoutError:
            pass
        semaphore = asyncio.Semaphore(self._max_concurrent)
        await asyncio.gather(*(self._process_instrument(i, semaphore) for i in missing))
        logger.info(
            "open_interest_bucket_retry",
            extra={"boundary": boundary.isoformat(), "retried": len(missing)},
        )

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
            await OpenInterestRepository(session).delete_before(datetime.now(UTC) - self._retention)

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
                    repo = OpenInterestRepository(session)

                    await self._bootstrap_step(session, repo, instrument, symbol)
                    await self._catchup_step(session, repo, instrument, symbol)
            except Exception:  # noqa: BLE001 - one bad symbol must not stop the round
                logger.exception(
                    "open_interest_reconciliation_step_failed",
                    extra={"instrument_id": instrument_id},
                )
            await asyncio.sleep(self._request_delay)

    # -- backward bootstrap: oi_synced_from -> now - required_warmup ------------

    async def _bootstrap_step(
        self,
        session: AsyncSession,
        repo: OpenInterestRepository,
        instrument: Instrument,
        symbol: str,
    ) -> None:
        now = datetime.now(UTC)
        target_start = now - self._required_warmup
        synced_from = instrument.oi_synced_from

        if synced_from is not None and synced_from <= target_start:
            return  # already bootstrapped far enough back

        # One bounded page per round, walking backward from the current
        # frontier — same shape as HistoryReconciler._bootstrap_step, just
        # against a much shorter target window.
        end = synced_from if synced_from is not None else now
        start = target_start
        if end <= start:
            instrument.oi_synced_from = target_start
            await session.commit()
            return

        try:
            raw = await self._bybit_client.get_open_interest(
                category=_CATEGORY,
                symbol=symbol,
                interval_time=_INTERVAL_TIME,
                start=int(start.timestamp() * 1000),
                end=int(end.timestamp() * 1000) - 1,
                limit=_PAGE_SIZE,
            )
        except BybitApiError as exc:
            logger.warning(
                "open_interest_bootstrap_page_failed", extra={"symbol": symbol, "error": str(exc)}
            )
            return

        result = OpenInterestHistoryResult.model_validate(raw)
        if not result.list:
            # Nothing left between target_start and the current frontier:
            # reached the natural floor (a new listing, or the exchange's
            # own OI history limit) — accept it as the true start, never
            # manufacture data to fill it. Same convention as
            # HistoryReconciler._bootstrap_step's empty-page handling.
            instrument.oi_synced_from = target_start
            await session.commit()
            return

        await repo.upsert_many(_to_rows(instrument.id, result, datetime.now(UTC)))

        oldest_ms = min(int(entry.timestamp) for entry in result.list)
        instrument.oi_synced_from = datetime.fromtimestamp(oldest_ms / 1000, tz=UTC)
        await session.commit()

    # -- forward catch-up: latest persisted observation -> now ------------------

    async def _catchup_step(
        self,
        session: AsyncSession,
        repo: OpenInterestRepository,
        instrument: Instrument,
        symbol: str,
    ) -> None:
        """Recover everything after the latest stored observation, oldest
        first, one bounded window at a time (`CATCHUP_WINDOW`, up to
        `MAX_CATCHUP_WINDOWS_PER_ROUND` per round). Each window is fetched
        completely (`fetch_open_interest_window`) before the next, so the
        stored maximum only ever advances over windows that were actually
        fetched — a gap can no longer be sealed by a newest-first page.

        A window Bybit has no data for (an exchange-side gap) doesn't stop
        the walk: the next window is tried in the same round. If a round
        ends inside such an empty stretch, the next round re-scans it from
        the same maximum — bounded, and it resolves as soon as data
        resumes."""
        # Captured before any upsert: `upsert_many` expires the session's
        # objects, and lazily reloading `instrument.id` from inside this
        # async call would raise `MissingGreenlet` (same pitfall
        # `HistoryReconciler._catchup_step` documents).
        instrument_id = instrument.id
        latest = await repo.fetch_latest_observed_at_per_instrument([instrument_id])
        since = latest.get(instrument_id)
        if since is None:
            return  # nothing bootstrapped yet this round — next round's bootstrap seeds it

        now = datetime.now(UTC)
        window_start = since + timedelta(milliseconds=1)
        for _ in range(MAX_CATCHUP_WINDOWS_PER_ROUND):
            if window_start >= now:
                return
            window_end = min(now, since + CATCHUP_WINDOW)
            try:
                result = await fetch_open_interest_window(
                    self._bybit_client, symbol, window_start, window_end
                )
            except BybitApiError as exc:
                logger.warning(
                    "open_interest_catchup_page_failed",
                    extra={"symbol": symbol, "error": str(exc)},
                )
                return  # retried from the same stored maximum next round
            if result.list:
                await repo.upsert_many(_to_rows(instrument_id, result, datetime.now(UTC)))
                since = max(
                    datetime.fromtimestamp(int(e.timestamp) / 1000, tz=UTC) for e in result.list
                )
            else:
                since = window_end
            window_start = since + timedelta(milliseconds=1)
            if window_end >= now:
                return
