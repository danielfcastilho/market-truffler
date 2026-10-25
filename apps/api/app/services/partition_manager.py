"""Operational lifecycle for monthly range partitions.

Partition provisioning/retention is deliberately kept out of Alembic: each
migration creates just enough partitions for the app to start, and this
service owns the ongoing, idempotent work of keeping a rolling window of
partitions present (`ensure_partitions`) and dropping whole partitions once
they've aged out of the retention horizon (`drop_expired_partitions`).
Retention this way is a handful of `DROP TABLE` statements, never a
row-by-row delete across hundreds of millions of rows.

Shared by every RANGE-partitioned table MARKET owns (`market_candles`,
`market_frame_members`) — both partition on a UTC timestamp column with the
same monthly scheme, so one implementation serves both via the `table`/
`partition_column` parameters rather than duplicating this module per table.

Both functions are no-ops on non-PostgreSQL engines (the SQLite engine used
in tests) since only PostgreSQL is partitioned — SQLite tests exercise a
single flat table, which is sufficient at test data volumes.

Lock waits are bounded. Creating or dropping a partition needs an ACCESS
EXCLUSIVE lock on the parent, which waits behind every reader of the table
(a research shard read can last minutes) and, while waiting, queues every
later reader and writer — frame building included — behind itself. With
`lock_timeout_ms` the statement gives up instead (SQLSTATE 55P03), the
caller rolls the transaction back and retries later: see
`app.services.partition_maintenance`. The timeout is `SET LOCAL`, so it ends
with the transaction and never stays on a pooled connection.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.frame import MarketFrameMember

logger = logging.getLogger(__name__)


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month)


def _partition_name(table: str, month_start: datetime) -> str:
    return f"{table}_{month_start.year:04d}_{month_start.month:02d}"


def _partition_name_re(table: str) -> re.Pattern[str]:
    return re.compile(rf"^{table}_(\d{{4}})_(\d{{2}})$")


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


#: SQLSTATE → category of a failed partition statement. Only `lock_timeout`
#: and `deadlock` are contention, worth retrying shortly; anything else is a
#: real fault (missing table/schema, privileges, a bug) and is never
#: disguised as contention.
_ERROR_CATEGORIES = {
    "55P03": "lock_timeout",
    "40P01": "deadlock",
    "42P01": "missing_schema",
    "3F000": "missing_schema",
    "42501": "permission",
}
RETRYABLE_CATEGORIES = frozenset({"lock_timeout", "deadlock"})


def error_category(exc: BaseException) -> str:
    if isinstance(exc, DBAPIError):
        sqlstate = getattr(exc.orig, "sqlstate", None)
        return _ERROR_CATEGORIES.get(sqlstate or "", "unexpected")
    return "unexpected"


def retained_months(now: datetime, retention_days: int) -> list[datetime]:
    """Months whose partitions retention keeps: from the retention cutoff's
    month (`drop_expired_partitions` drops everything before it) through
    next month. Creating anything older would only be dropped again."""
    month = _month_start(now - timedelta(days=retention_days))
    last = _add_months(_month_start(now), 1)
    months = []
    while month <= last:
        months.append(month)
        month = _add_months(month, 1)
    return months


async def _bound_lock_wait(session: AsyncSession, lock_timeout_ms: int | None) -> None:
    if lock_timeout_ms is not None:
        # SET takes no bind parameters; an int is safe to inline.
        await session.execute(text(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"))


async def existing_partitions(session: AsyncSession, table: str) -> set[str]:
    if not _is_postgres(session):
        return set()
    result = await session.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in result}


async def ensure_partitions(
    session: AsyncSession,
    *,
    months: list[datetime],
    table: str = "market_candles",
    lock_timeout_ms: int | None = None,
) -> list[str]:
    """Create the monthly partitions of `table` for `months` that don't
    exist yet; returns those created.

    An existing partition costs one catalog read and takes no lock on the
    parent, so a restart with everything in place locks nothing. Each
    missing partition is created in its own short transaction, so a lock
    timeout on one never undoes another. Idempotent and safe concurrently
    (`IF NOT EXISTS`).
    """
    if not _is_postgres(session):
        return []

    existing = await existing_partitions(session, table)
    await session.commit()

    created: list[str] = []
    for month in months:
        name = _partition_name(table, month)
        if name in existing:
            continue
        next_month = _add_months(month, 1)
        await _bound_lock_wait(session, lock_timeout_ms)
        # PostgreSQL DDL doesn't accept bound parameters for partition bounds
        # (`FOR VALUES FROM ($1) TO ($2)` is rejected outright), so these are
        # inlined as ISO literals — safe here since both are internally
        # computed datetimes, never user input, exactly like the Alembic
        # migrations that create these tables' initial partitions.
        try:
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "  # noqa: S608
                    f"FOR VALUES FROM ('{month.isoformat()}') TO ('{next_month.isoformat()}')"
                )
            )
            await session.commit()
        except DBAPIError as exc:
            # Another process created it while this one waited for the lock:
            # IF NOT EXISTS was checked before the wait (SQLSTATE 42P07).
            if getattr(exc.orig, "sqlstate", None) != "42P07":
                raise
            await session.rollback()
            continue
        created.append(name)

    if created:
        logger.info("partitions_created", extra={"table": table, "partitions": created})
    return created


async def _has_candle_references(session: AsyncSession, start: datetime, end: datetime) -> bool:
    """Frame members can reference arbitrarily stale candles, across month boundaries."""
    columns = (
        MarketFrameMember.open_time_5m,
        MarketFrameMember.open_time_15m,
        MarketFrameMember.open_time_1h,
        # Nullable (see MarketFrameMember.h4/h24) — a NULL comparison is
        # simply never true in the OR below, which is exactly correct: a
        # member with no 4h/24h reference makes no 4h/24h-based retention
        # claim.
        MarketFrameMember.open_time_4h,
        MarketFrameMember.open_time_24h,
    )
    return bool(
        await session.scalar(
            select(exists().where(or_(*((column >= start) & (column < end) for column in columns))))
        )
    )


async def drop_expired_partitions(
    session: AsyncSession,
    *,
    retention_days: int,
    table: str = "market_candles",
    lock_timeout_ms: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Drop whole partitions of `table` entirely older than the retention horizon.

    Only ever drops a partition whose entire month is before the retention
    cutoff — a partition that might still hold retained data is never
    touched, and no row is ever deleted individually.
    """
    if not _is_postgres(session):
        return []

    cutoff_month = _month_start((now or datetime.now(UTC)) - timedelta(days=retention_days))

    await _bound_lock_wait(session, lock_timeout_ms)
    partition_names = sorted(await existing_partitions(session, table))
    name_re = _partition_name_re(table)

    dropped: list[str] = []
    locked = False
    for name in partition_names:
        match = name_re.match(name)
        if match is None:
            continue
        month = datetime(int(match.group(1)), int(match.group(2)), 1, tzinfo=UTC)
        if month < cutoff_month:
            if table == "market_candles":
                # Unlocked pre-check first: a month still referenced is kept
                # without taking the exclusive locks at all (it is re-checked
                # under them before anything is dropped).
                if await _has_candle_references(session, month, _add_months(month, 1)):
                    logger.info("candle_partition_retained_for_frames", extra={"partition": name})
                    continue
                if not locked:
                    # Match frame construction's lock order. Keep reference checks
                    # and drops atomic with respect to concurrent frame builders.
                    await session.execute(
                        text("LOCK TABLE market_candles IN ACCESS EXCLUSIVE MODE")
                    )
                    await session.execute(
                        text("LOCK TABLE market_frame_members IN ACCESS EXCLUSIVE MODE")
                    )
                    locked = True
                if await _has_candle_references(session, month, _add_months(month, 1)):
                    logger.info("candle_partition_retained_for_frames", extra={"partition": name})
                    continue
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))  # noqa: S608
            dropped.append(name)

    await session.commit()
    if dropped:
        logger.info("partitions_dropped", extra={"table": table, "partitions": dropped})

    return dropped
