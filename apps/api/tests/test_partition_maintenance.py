"""Partition maintenance state machine (app.services.partition_maintenance):
required vs deferrable work, retries, error classification, readiness and
Vitals. Failures are injected here; the real PostgreSQL lock behavior is
tested in test_partition_locks_postgres.py."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import partition_manager
from app.services.partition_maintenance import PartitionMaintenance, RequiredPartitionError

TABLES = ("market_frame_members", "sniffer_results", "market_candles")
NOW = datetime(2026, 10, 25, 14, 28, tzinfo=UTC)


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def _lock_timeout() -> OperationalError:
    return OperationalError("CREATE TABLE ...", {}, _Orig("55P03"))


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


def _maintenance(session_factory, **kwargs) -> PartitionMaintenance:
    kwargs.setdefault("retry_delays", (0.01, 0.01, 0.01))
    kwargs.setdefault("required_retry_delays", (0.01,))
    return PartitionMaintenance(
        session_factory, tables=TABLES, retention_days=30, clock=lambda: NOW, **kwargs
    )


def _failing(monkeypatch, name: str, errors: list[Exception]) -> list[dict]:
    """Replace a partition_manager operation: raise `errors` in turn, then
    succeed. Returns the recorded calls."""
    calls: list[dict] = []

    async def op(session, **kwargs):
        calls.append(kwargs)
        if errors:
            raise errors.pop(0)
        return []

    monkeypatch.setattr(partition_manager, name, op)
    return calls


async def test_an_uncontended_pass_is_healthy(session_factory):
    maintenance = _maintenance(session_factory)
    await maintenance.ensure_required()
    assert await maintenance.maintain() is False
    status = maintenance.status
    assert status.state == "healthy" and status.pending == []
    assert status.last_success_at == NOW and not status.ingestion_affected


async def test_startup_only_requires_the_current_month(session_factory, monkeypatch):
    creates = _failing(monkeypatch, "ensure_partitions", [])
    drops = _failing(monkeypatch, "drop_expired_partitions", [])
    await _maintenance(session_factory).ensure_required()
    assert [c["months"] for c in creates] == [[datetime(2026, 10, 1, tzinfo=UTC)]] * 3
    assert all(c["lock_timeout_ms"] == 2000 for c in creates)
    assert drops == []  # retention never runs at startup


async def test_contention_on_deferrable_work_is_retried_until_it_succeeds(
    session_factory, monkeypatch
):
    _failing(monkeypatch, "ensure_partitions", [_lock_timeout(), _lock_timeout()])
    _failing(monkeypatch, "drop_expired_partitions", [])
    maintenance = _maintenance(session_factory)
    assert await maintenance.maintain() is True
    status = maintenance.status
    assert status.state == "deferred"
    assert status.pending == ["create market_frame_members", "create sniffer_results"]
    assert status.last_error_category == "lock_timeout" and not status.ingestion_affected
    maintenance.request()
    await maintenance._task
    assert status.state == "healthy" and status.retry_count == 0 and status.pending == []


async def test_contention_that_persists_stops_after_bounded_retries(session_factory, monkeypatch):
    calls = _failing(monkeypatch, "ensure_partitions", [_lock_timeout()] * 100)
    _failing(monkeypatch, "drop_expired_partitions", [])
    maintenance = _maintenance(session_factory, retry_delays=(0.01, 0.01))
    maintenance.request()
    await asyncio.wait_for(maintenance._task, timeout=5)
    # 1 attempt + 2 retries, each over the three tables.
    assert len(calls) == 9
    status = maintenance.status
    # Exhausted: deferred to the next universe refresh, no retry scheduled.
    assert status.state == "deferred" and status.retry_count == 2
    assert status.next_retry_at is None
    assert status.pending == [f"create {t}" for t in TABLES]
    # The next universe refresh rediscovers and retries it.
    maintenance.request()
    await asyncio.wait_for(maintenance._task, timeout=5)
    assert len(calls) == 18


async def test_deadlocks_are_retried_like_lock_timeouts(session_factory, monkeypatch):
    _failing(monkeypatch, "ensure_partitions", [])
    _failing(
        monkeypatch,
        "drop_expired_partitions",
        [OperationalError("LOCK TABLE ...", {}, _Orig("40P01"))],
    )
    maintenance = _maintenance(session_factory)
    assert await maintenance.maintain() is True
    assert maintenance.status.last_error_category == "deadlock"
    assert maintenance.status.pending == ["retention market_frame_members"]


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ProgrammingError("CREATE ...", {}, _Orig("42P01")), "missing_schema"),
        (ProgrammingError("CREATE ...", {}, _Orig("42501")), "permission"),
        (OperationalError("CREATE ...", {}, _Orig("08006")), "unexpected"),
        (RuntimeError("bug"), "unexpected"),
    ],
)
async def test_other_errors_fail_and_are_never_deferred(
    session_factory, monkeypatch, error, category
):
    _failing(monkeypatch, "ensure_partitions", [error])
    _failing(monkeypatch, "drop_expired_partitions", [])
    maintenance = _maintenance(session_factory)
    maintenance.request()
    await maintenance._task
    status = maintenance.status
    assert status.state == "failed" and status.last_error_category == category
    assert status.retry_count == 0 and status.next_retry_at is None


async def test_a_blocked_required_partition_holds_startup_until_it_exists(
    session_factory, monkeypatch
):
    _failing(monkeypatch, "ensure_partitions", [_lock_timeout()] * 3)
    maintenance = _maintenance(session_factory, required_retry_delays=(0.05,))
    startup = asyncio.create_task(maintenance.ensure_required())
    await asyncio.sleep(0.02)
    assert not startup.done()
    assert maintenance.status.state == "required_unavailable"
    assert maintenance.status.ingestion_affected
    await asyncio.wait_for(startup, timeout=5)
    assert not maintenance.status.ingestion_affected
    assert maintenance.status.state == "pending" and maintenance.status.pending == []


async def test_a_required_partition_fault_fails_startup(session_factory, monkeypatch):
    _failing(monkeypatch, "ensure_partitions", [ProgrammingError("x", {}, _Orig("42501"))])
    maintenance = _maintenance(session_factory)
    with pytest.raises(RequiredPartitionError, match="permission"):
        await maintenance.ensure_required()
    assert maintenance.status.state == "failed"


async def test_a_missing_current_partition_at_runtime_is_required_unavailable(
    session_factory, monkeypatch
):
    _failing(monkeypatch, "ensure_partitions", [_lock_timeout()])
    _failing(monkeypatch, "drop_expired_partitions", [])
    maintenance = _maintenance(session_factory)

    async def missing() -> bool:
        return True

    monkeypatch.setattr(maintenance, "_ingestion_affected", missing)
    await maintenance.maintain()
    assert maintenance.status.state == "required_unavailable"
    assert maintenance.status.ingestion_affected


async def test_maintenance_never_runs_twice_at_once(session_factory, monkeypatch):
    running = 0
    peak = 0

    async def slow(session, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return []

    monkeypatch.setattr(partition_manager, "ensure_partitions", slow)
    monkeypatch.setattr(partition_manager, "drop_expired_partitions", slow)
    maintenance = _maintenance(session_factory)
    maintenance.request()
    task = maintenance._task
    maintenance.request()  # covered by the run in progress
    assert maintenance._task is task
    await asyncio.gather(task, maintenance.maintain(), maintenance.ensure_required())
    assert peak == 1


async def test_stop_cancels_a_pending_retry(session_factory, monkeypatch):
    _failing(monkeypatch, "ensure_partitions", [_lock_timeout()] * 100)
    _failing(monkeypatch, "drop_expired_partitions", [])
    maintenance = _maintenance(session_factory, retry_delays=(60,))
    maintenance.request()
    await asyncio.sleep(0.05)
    assert maintenance.status.state == "retrying" and maintenance.status.next_retry_at
    await asyncio.wait_for(maintenance.stop(), timeout=1)
    assert maintenance._task is None


# -- readiness and Vitals -------------------------------------------------------------


class _Reconciler:
    def __init__(self, maintenance: PartitionMaintenance) -> None:
        self.partition_maintenance = maintenance


async def test_readiness_fails_only_when_ingestion_is_affected(client, session_factory):
    from app.main import app

    maintenance = _maintenance(session_factory)
    app.state.history_reconciler = _Reconciler(maintenance)
    try:
        maintenance.status.state = "retrying"  # deferred upkeep: still ready
        assert (await client.get("/ready")).status_code == 200
        maintenance.status.state = "required_unavailable"
        maintenance.status.ingestion_affected = True
        response = await client.get("/ready")
        assert response.status_code == 503
        assert "partition" in response.json()["detail"]
    finally:
        del app.state.history_reconciler


async def test_market_status_reports_partition_maintenance(client, test_user, session_factory):
    from test_market_router import _OK_RECONCILER_STATUS, _clear_overrides, _login, _override

    from app.core.deps import get_history_reconciler
    from app.main import app

    maintenance = _maintenance(session_factory)
    maintenance.status.state = "retrying"
    maintenance.status.retry_count = 2
    maintenance.status.last_error_category = "lock_timeout"
    maintenance.status.pending = ["create market_candles"]

    class _WithMaintenance(_Reconciler):
        status = _OK_RECONCILER_STATUS

    _override()
    app.dependency_overrides[get_history_reconciler] = lambda: _WithMaintenance(maintenance)
    try:
        await _login(client, test_user)
        body = (await client.get("/api/market/status")).json()
    finally:
        _clear_overrides()
    state = body["partition_maintenance"]
    assert state["state"] == "retrying" and state["retry_count"] == 2
    assert state["pending"] == ["create market_candles"]
    assert state["last_error_category"] == "lock_timeout"
    assert state["ingestion_affected"] is False
