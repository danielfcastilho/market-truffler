"""Partition upkeep that cannot hang startup or stall ingestion.

Two kinds of work, on the same monthly partitions
(`app.services.partition_manager`):

- **Required** (`ensure_required`, at startup, before anything ingests): the
  current month's partition of every partitioned table — live candles,
  frame members and Sniffer results are written there right now. Without it
  inserts fail, so startup does not continue: it retries until the partition
  exists (each attempt's lock wait is bounded, so research reads are never
  queued behind it for long) and the API stays not ready meanwhile. A
  non-contention error fails startup outright, as before.
- **Deferrable** (`request`, after each universe refresh): the rest of the
  retained window — next month's partition, created about a month before
  it is needed, and older months the history backfill writes into — plus
  retention drops. On lock contention the operation is deferred and retried
  with backoff by one in-process task; the next refresh (or a restart)
  rediscovers whatever is still missing from the catalog, so nothing needs
  a durable queue.

On 2026-10-25 a restart re-created two already-expired months (only to drop
them two seconds later); creating them waited for ACCESS EXCLUSIVE behind a
research shard read with no timeout, and startup hung for seven minutes.
Expired months are no longer created, and every lock wait is bounded.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import partition_manager

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

MaintenanceState = Literal[
    "pending", "healthy", "deferred", "retrying", "failed", "required_unavailable"
]


@dataclass
class PartitionMaintenanceStatus:
    """For Vitals. `pending` lists the operations not done yet ("create
    market_candles_2026_12", "retention sniffer_results"); error categories
    come from `partition_manager.error_category`, never raw database
    messages (those are logged)."""

    state: MaintenanceState = "pending"
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    retry_count: int = 0
    next_retry_at: datetime | None = None
    last_error_category: str | None = None
    pending: list[str] = field(default_factory=list)
    ingestion_affected: bool = False


@dataclass
class _Pass:
    deferred: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    category: str | None = None


class RequiredPartitionError(RuntimeError):
    """A required partition failed for a reason other than contention."""


class PartitionMaintenance:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        tables: Sequence[str],
        retention_days: int,
        lock_timeout_ms: int = 2000,
        retry_delays: Sequence[float] = (15, 30, 60, 120, 240),
        required_retry_delays: Sequence[float] = (1, 2, 5, 10),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        # Order matters for retention: members expire before the candle
        # months they reference are checked.
        self._session_factory = session_factory
        self._tables = tuple(tables)
        self._retention_days = retention_days
        self._lock_timeout_ms = lock_timeout_ms
        self._retry_delays = tuple(retry_delays)
        self._required_retry_delays = tuple(required_retry_delays)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.status = PartitionMaintenanceStatus()

    # -- required, at startup ----------------------------------------------------

    async def ensure_required(self) -> None:
        """Return once the current month's partition of every table exists.
        Retries contention indefinitely (bounded per attempt, logged each
        time); raises `RequiredPartitionError` on any other failure."""
        attempt = 0
        while True:
            async with self._lock:
                result = await self._pass(required_only=True)
            if result.failed:
                self._record(result, state="failed")
                raise RequiredPartitionError(
                    f"required partitions failed ({result.category}): {', '.join(result.failed)}"
                )
            if not result.deferred:
                self.status.ingestion_affected = False
                if self.status.state == "required_unavailable":
                    self.status.state = "pending"
                    self.status.pending = []
                return
            delay = self._required_retry_delays[min(attempt, len(self._required_retry_delays) - 1)]
            attempt += 1
            self._record(result, state="required_unavailable")
            self.status.ingestion_affected = True
            self.status.retry_count = attempt
            self.status.next_retry_at = self._clock() + timedelta(seconds=delay)
            logger.warning(
                "partition_prerequisite_blocked",
                extra={"pending": result.deferred, "attempt": attempt, "retry_in": delay},
            )
            await asyncio.sleep(delay)

    # -- deferrable, in the background ------------------------------------------

    def request(self) -> None:
        """Run maintenance in the background; never blocks the caller. A run
        already in progress (or waiting to retry) covers this request."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        for attempt in range(len(self._retry_delays) + 1):
            deferred = await self.maintain()
            if not deferred:
                return
            if attempt == len(self._retry_delays):
                # Retries exhausted: left to the next universe refresh.
                logger.warning("partition_maintenance_retries_exhausted")
                return
            delay = self._retry_delays[attempt]
            self.status.retry_count += 1
            self.status.next_retry_at = self._clock() + timedelta(seconds=delay)
            if self.status.state == "deferred":
                self.status.state = "retrying"
            await asyncio.sleep(delay)

    async def maintain(self) -> bool:
        """One pass over the retained window and retention. Never raises;
        returns whether anything was deferred by contention."""
        async with self._lock:
            try:
                result = await self._pass(required_only=False)
            except Exception:  # noqa: BLE001 - recorded, never kills the task
                logger.exception("partition_maintenance_failed")
                result = _Pass(failed=["maintenance"], category="unexpected")
            affected = await self._ingestion_affected()
        if affected:
            state: MaintenanceState = "required_unavailable"
            logger.error("partition_required_missing", extra={"pending": result.deferred})
        elif result.failed:
            state = "failed"
        elif result.deferred:
            state = "deferred"
        else:
            state = "healthy"
        self._record(result, state=state)
        self.status.ingestion_affected = affected
        if state == "healthy":
            self.status.retry_count = 0
            self.status.last_success_at = self.status.last_attempt_at
        return bool(result.deferred)

    # -- internals ---------------------------------------------------------------

    def _record(self, result: _Pass, *, state: MaintenanceState) -> None:
        self.status.state = state
        self.status.pending = [*result.failed, *result.deferred]
        if result.category is not None:
            self.status.last_error_category = result.category
        self.status.next_retry_at = None

    async def _pass(self, *, required_only: bool) -> _Pass:
        now = self._clock()
        self.status.last_attempt_at = now
        months = (
            [partition_manager._month_start(now)]
            if required_only
            else partition_manager.retained_months(now, self._retention_days)
        )
        result = _Pass()
        for table in self._tables:
            await self._attempt(
                result,
                f"create {table}",
                partial(
                    partition_manager.ensure_partitions,
                    months=months,
                    table=table,
                    lock_timeout_ms=self._lock_timeout_ms,
                ),
            )
            if not required_only:
                await self._attempt(
                    result,
                    f"retention {table}",
                    partial(
                        partition_manager.drop_expired_partitions,
                        retention_days=self._retention_days,
                        table=table,
                        lock_timeout_ms=self._lock_timeout_ms,
                        now=now,
                    ),
                )
        return result

    async def _attempt(
        self, result: _Pass, operation: str, run: Callable[[AsyncSession], Awaitable[object]]
    ) -> None:
        try:
            # Leaving the block closes the session, which rolls back a
            # failed transaction (and its SET LOCAL) and returns the
            # connection to the pool.
            async with self._session_factory() as session:
                await run(session)
        except Exception as exc:  # noqa: BLE001 - classified below, never hidden
            category = partition_manager.error_category(exc)
            result.category = category
            if category in partition_manager.RETRYABLE_CATEGORIES:
                result.deferred.append(operation)
                logger.warning(
                    "partition_operation_deferred",
                    extra={"operation": operation, "category": category},
                )
            else:
                result.failed.append(operation)
                logger.exception(
                    "partition_operation_failed",
                    extra={"operation": operation, "category": category},
                )

    async def _ingestion_affected(self) -> bool:
        """Whether a table lacks the partition for the current month."""
        month = partition_manager._month_start(self._clock())
        try:
            async with self._session_factory() as session:
                for table in self._tables:
                    if not partition_manager._is_postgres(session):
                        return False
                    names = await partition_manager.existing_partitions(session, table)
                    if partition_manager._partition_name(table, month) not in names:
                        return True
        except Exception:  # noqa: BLE001 - unknown is not "affected"
            logger.exception("partition_catalog_check_failed")
        return False
