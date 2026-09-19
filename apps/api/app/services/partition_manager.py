"""Operational lifecycle for `market_candles`' monthly range partitions.

Partition provisioning/retention is deliberately kept out of Alembic: the
migration creates just enough partitions for the app to start, and this
service owns the ongoing, idempotent work of keeping a rolling window of
partitions present (`ensure_partitions`) and dropping whole partitions once
they've aged out of the retention horizon (`drop_expired_partitions`).
Retention this way is a handful of `DROP TABLE` statements, never a
row-by-row delete across hundreds of millions of rows.

Both functions are no-ops on non-PostgreSQL engines (the SQLite engine used
in tests) since only PostgreSQL is partitioned — SQLite tests exercise a
single flat table, which is sufficient at test data volumes.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TABLE = "market_candles"
_PARTITION_NAME_RE = re.compile(rf"^{_TABLE}_(\d{{4}})_(\d{{2}})$")


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month)


def _partition_name(month_start: datetime) -> str:
    return f"{_TABLE}_{month_start.year:04d}_{month_start.month:02d}"


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def ensure_partitions(
    session: AsyncSession, *, months_back: int, months_forward: int = 1
) -> list[str]:
    """Idempotently create monthly partitions covering
    [this month - months_back, this month + months_forward].

    Safe to call repeatedly and concurrently — `IF NOT EXISTS` makes each
    statement a no-op once the partition exists.
    """
    if not _is_postgres(session):
        return []

    this_month = _month_start(datetime.now(UTC))
    start_month = _add_months(this_month, -months_back)
    total_months = months_back + months_forward + 1

    created: list[str] = []
    for i in range(total_months):
        month = _add_months(start_month, i)
        next_month = _add_months(month, 1)
        name = _partition_name(month)
        # PostgreSQL DDL doesn't accept bound parameters for partition bounds
        # (`FOR VALUES FROM ($1) TO ($2)` is rejected outright), so these are
        # inlined as ISO literals — safe here since both are internally
        # computed datetimes, never user input, exactly like the Alembic
        # migration that creates this table's initial partitions.
        await session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {_TABLE} "  # noqa: S608
                f"FOR VALUES FROM ('{month.isoformat()}') TO ('{next_month.isoformat()}')"
            )
        )
        created.append(name)

    await session.commit()
    logger.info(
        "market_candles_partitions_ensured",
        extra={"months_back": months_back, "months_forward": months_forward},
    )
    return created


async def drop_expired_partitions(session: AsyncSession, *, retention_days: int) -> list[str]:
    """Drop whole partitions entirely older than the retention horizon.

    Only ever drops a partition whose entire month is before the retention
    cutoff — a partition that might still hold retained data is never
    touched, and no row is ever deleted individually.
    """
    if not _is_postgres(session):
        return []

    cutoff_month = _month_start(datetime.now(UTC) - timedelta(days=retention_days))

    result = await session.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :table"
        ),
        {"table": _TABLE},
    )
    partition_names = [row[0] for row in result]

    dropped: list[str] = []
    for name in partition_names:
        match = _PARTITION_NAME_RE.match(name)
        if match is None:
            continue
        month = datetime(int(match.group(1)), int(match.group(2)), 1, tzinfo=UTC)
        if month < cutoff_month:
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))  # noqa: S608
            dropped.append(name)

    if dropped:
        await session.commit()
        logger.info("market_candles_partitions_dropped", extra={"partitions": dropped})

    return dropped
