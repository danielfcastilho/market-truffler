"""Partition lock contention against a real PostgreSQL — the 2026-10-25
incident and its fix. SQLite has no partitions or table locks, so these run
only with TEST_POSTGRES_URL set (each test uses its own throwaway schema and
drops it), e.g.:

    docker run -d --rm --name mt-pgtest -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t \\
        -e POSTGRES_DB=t -p 127.0.0.1:55432:5432 postgres:17-alpine
    TEST_POSTGRES_URL=postgresql+psycopg://t:t@127.0.0.1:55432/t \\
        .venv/bin/pytest tests/test_partition_locks_postgres.py

The blocker is what the research worker's shard read holds: an open
transaction that has read the partitioned parent (ACCESS SHARE). Creating or
dropping a partition needs ACCESS EXCLUSIVE on that parent.
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.services import partition_manager as pm
from app.services.partition_maintenance import PartitionMaintenance

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")

TABLES = ("market_frame_members", "sniffer_results", "market_candles")
RETENTION_DAYS = 30
LOCK_TIMEOUT_MS = 300
# Generous: a bounded wait is ~LOCK_TIMEOUT_MS per blocked statement; the
# incident's unbounded wait lasted seven minutes.
BOUND_SECONDS = 3.0


def _now() -> datetime:
    return datetime.now(UTC)


def _window() -> list[datetime]:
    return pm.retained_months(_now(), RETENTION_DAYS)


def _expired_month() -> datetime:
    return pm._add_months(_window()[0], -1)


@dataclass
class PG:
    app: AsyncEngine
    blocker: AsyncEngine
    options: dict[str, str]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter((self.app, self.blocker))


@pytest_asyncio.fixture
async def pg() -> AsyncGenerator[PG]:
    """App and blocker engines on a fresh schema with the production
    partition layout: every table partitioned over the retained window. The
    app engine has a single pooled connection, so a later checkout reuses the
    connection a failed statement ran on."""
    assert URL is not None
    schema = f"pm_test_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(URL)
    async with admin.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
    options = {"options": f"-csearch_path={schema}"}
    app = create_async_engine(URL, connect_args=options, pool_size=1, max_overflow=0)
    blocker = create_async_engine(URL, connect_args=options)
    try:
        async with app.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=app, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            for table in TABLES:
                await pm.ensure_partitions(session, months=_window(), table=table)
        yield PG(app, blocker, options)
    finally:
        await app.dispose()
        await blocker.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


def _maintenance(engine: AsyncEngine, **kwargs) -> PartitionMaintenance:
    kwargs.setdefault("retry_delays", (0.05,))
    kwargs.setdefault("required_retry_delays", (0.2,))
    return PartitionMaintenance(
        _factory(engine),
        tables=kwargs.pop("tables", TABLES),
        retention_days=RETENTION_DAYS,
        lock_timeout_ms=LOCK_TIMEOUT_MS,
        **kwargs,
    )


async def _research_read(blocker: AsyncEngine, table: str) -> AsyncConnection:
    """Open a transaction holding ACCESS SHARE on `table`'s parent, as a
    research shard read does while it runs. Roll it back to release."""
    conn = await blocker.connect()
    await conn.begin()
    await conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    return conn


async def _partitions(engine: AsyncEngine, table: str) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent "
                "WHERE p.relname = :t ORDER BY 1"
            ),
            {"t": table},
        )
        return [r[0] for r in rows]


async def _drop(engine: AsyncEngine, name: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE {name}"))  # noqa: S608


async def _create(engine: AsyncEngine, table: str, month: datetime) -> None:
    async with _factory(engine)() as session:
        await pm.ensure_partitions(session, months=[month], table=table)


async def _assert_clean_connection(engine: AsyncEngine) -> None:
    """The failed transaction was rolled back: the (only) pooled connection
    is idle, holds no locks and its lock_timeout is back to the default."""
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW lock_timeout"))).scalar() == "0"
        held = await conn.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid() "
                "AND locktype = 'relation' AND relation::regclass::text LIKE ANY (:t)"
            ),
            {"t": [f"{t}%" for t in TABLES]},
        )
        assert held.scalar() == 0


# -- the incident ---------------------------------------------------------------------


async def test_the_incident_reproduced_unbounded_ddl_waits_behind_a_research_read(pg):
    """What happened on 2026-10-25: startup re-created an expired month's
    partition, and with no lock timeout it queued behind the research read
    — and every later reader of the table queued behind it."""
    app, blocker = pg
    research = await _research_read(blocker, "market_frame_members")
    month = _expired_month()
    name = pm._partition_name("market_frame_members", month)
    ddl_conn = await blocker.connect()
    pid = (await ddl_conn.execute(text("SELECT pg_backend_pid()"))).scalar()
    ddl = asyncio.create_task(
        ddl_conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF market_frame_members "
                f"FOR VALUES FROM ('{month.isoformat()}') "
                f"TO ('{pm._add_months(month, 1).isoformat()}')"
            )
        )
    )
    try:
        await asyncio.sleep(1.0)
        assert not ddl.done()  # still waiting: no bound
        async with app.connect() as conn:
            waiting = await conn.execute(
                text("SELECT mode FROM pg_locks WHERE pid = :pid AND NOT granted"), {"pid": pid}
            )
            assert waiting.scalar() == "AccessExclusiveLock"
            # A frame build's read now queues behind the waiting DDL too.
            await conn.execute(text("SET lock_timeout = 500"))
            with pytest.raises(Exception, match="lock timeout"):
                await conn.execute(text("SELECT count(*) FROM market_frame_members"))
            await conn.rollback()
            await conn.execute(text("SELECT pg_cancel_backend(:pid)"), {"pid": pid})
    finally:
        with pytest.raises(Exception, match="cancel"):
            await ddl
        await ddl_conn.close()
        await research.rollback()
        await research.close()


async def test_startup_completes_while_research_reads_every_partitioned_table(pg):
    """The regression guard: HistoryReconciler.start() — the code path that
    hung for seven minutes — finishes promptly with all three parents held
    by a research read. Fails if startup ever waits on a table lock again."""
    from test_history_reconciler import _FakeBybitClient, _reconciler

    app, blocker = pg
    readers = [await _research_read(blocker, t) for t in TABLES]
    reconciler = _reconciler(
        _factory(app),
        _FakeBybitClient(),
        retention_days=RETENTION_DAYS,
        partition_lock_timeout_ms=LOCK_TIMEOUT_MS,
    )
    try:
        started = time.monotonic()
        await asyncio.wait_for(reconciler.start(), timeout=BOUND_SECONDS)
        elapsed = time.monotonic() - started
        await asyncio.wait_for(reconciler.partition_maintenance._task, timeout=BOUND_SECONDS)
        print(f"\nstartup under research reads: {elapsed * 1000:.0f} ms")
        # Everything required already exists: no lock was needed at all.
        assert reconciler.partition_maintenance.status.state == "healthy"
    finally:
        await reconciler.stop()
        for reader in readers:
            await reader.rollback()
            await reader.close()


# -- deferrable work ------------------------------------------------------------------


async def test_contended_upcoming_partition_is_deferred_bounded_and_retried(pg):
    app, blocker = pg
    upcoming = _window()[-1]
    name = pm._partition_name("market_frame_members", upcoming)
    await _drop(app, name)
    research = await _research_read(blocker, "market_frame_members")
    maintenance = _maintenance(app)
    try:
        started = time.monotonic()
        assert await maintenance.maintain() is True
        blocked = time.monotonic() - started
        print(f"\ndeferred after {blocked * 1000:.0f} ms (lock_timeout {LOCK_TIMEOUT_MS} ms)")
        assert LOCK_TIMEOUT_MS / 1000 <= blocked < BOUND_SECONDS
        status = maintenance.status
        assert status.state == "deferred" and status.pending == ["create market_frame_members"]
        assert status.last_error_category == "lock_timeout"
        assert not status.ingestion_affected  # the current month is there
        assert name not in await _partitions(app, "market_frame_members")
        await _assert_clean_connection(app)
        # Retrying while still blocked stays bounded and deferred.
        maintenance.request()
        await asyncio.wait_for(maintenance._task, timeout=BOUND_SECONDS * 3)
        assert maintenance.status.state == "deferred"
    finally:
        await research.rollback()
        await research.close()

    # A restarted API rediscovers the missing partition from the catalog.
    await maintenance.stop()
    restarted = _maintenance(app)
    started = time.monotonic()
    assert await restarted.maintain() is False
    print(f"retry after release: {(time.monotonic() - started) * 1000:.0f} ms")
    assert restarted.status.state == "healthy"
    assert await _partitions(app, "market_frame_members") == sorted(
        pm._partition_name("market_frame_members", m) for m in _window()
    )


async def test_contended_retention_is_deferred_then_drops_on_retry(pg):
    app, blocker = pg
    expired = pm._partition_name("sniffer_results", _expired_month())
    await _create(app, "sniffer_results", _expired_month())
    research = await _research_read(blocker, "sniffer_results")
    maintenance = _maintenance(app)
    try:
        assert await maintenance.maintain() is True
        assert maintenance.status.pending == ["retention sniffer_results"]
        assert expired in await _partitions(app, "sniffer_results")
        await _assert_clean_connection(app)
    finally:
        await research.rollback()
        await research.close()
    assert await maintenance.maintain() is False
    assert expired not in await _partitions(app, "sniffer_results")


async def test_concurrent_maintenance_creates_each_partition_once(pg):
    """Two processes (e.g. an old and a new API during a deploy) maintaining
    the same window at once: no error, no duplicate."""
    app, _ = pg
    upcoming = _window()[-1]
    for table in TABLES:
        await _drop(app, pm._partition_name(table, upcoming))
    other = create_async_engine(URL, connect_args=pg.options)
    try:
        first, second = _maintenance(app), _maintenance(other)
        await asyncio.gather(first.maintain(), second.maintain())
        # The loser of a create race sees "already exists" (42P07): done,
        # not a failure. A lock timeout while the winner holds it is a
        # normal deferral.
        assert {first.status.state, second.status.state} <= {"healthy", "deferred"}
        if "deferred" in (first.status.state, second.status.state):
            await first.maintain()
    finally:
        await other.dispose()
    for table in TABLES:
        names = await _partitions(app, table)
        assert names == sorted(set(names))
        assert pm._partition_name(table, upcoming) in names


# -- required work --------------------------------------------------------------------


async def test_contended_required_partition_holds_startup_until_released(pg):
    app, blocker = pg
    current = pm._month_start(_now())
    name = pm._partition_name("sniffer_results", current)
    await _drop(app, name)
    research = await _research_read(blocker, "sniffer_results")
    maintenance = _maintenance(app)
    startup = asyncio.create_task(maintenance.ensure_required())
    try:
        await asyncio.sleep(1.5)
        # Not ready: retrying with bounded waits, never proceeding without it.
        assert not startup.done()
        assert maintenance.status.state == "required_unavailable"
        assert maintenance.status.ingestion_affected
        assert maintenance.status.retry_count >= 2
        # Between attempts nothing waits on the table: a frame build's read
        # is delayed by at most one attempt's lock_timeout.
        async with blocker.connect() as conn:
            started = time.monotonic()
            await conn.execute(text("SELECT count(*) FROM sniffer_results"))
            assert time.monotonic() - started < LOCK_TIMEOUT_MS / 1000 + 0.5
    finally:
        await research.rollback()
        await research.close()
    await asyncio.wait_for(startup, timeout=BOUND_SECONDS)
    assert name in await _partitions(app, "sniffer_results")
    assert not maintenance.status.ingestion_affected


async def test_a_missing_table_fails_and_is_never_treated_as_contention(pg):
    app, _ = pg
    maintenance = _maintenance(app, tables=("no_such_table",))
    assert await maintenance.maintain() is False  # not deferred: no contention retry
    status = maintenance.status
    assert status.last_error_category == "missing_schema"
    assert status.pending == ["create no_such_table"]
    # Without the table there is no partition to ingest into either.
    assert status.state == "required_unavailable" and status.ingestion_affected
    await _assert_clean_connection(app)
