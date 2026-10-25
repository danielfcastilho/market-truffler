"""The research generation lock against a real PostgreSQL: session-level
advisory lock ownership, transaction state, pooling, and session loss.
SQLite has no advisory locks, so these run only with TEST_POSTGRES_URL set:

    docker run -d --rm --name mt-pgtest -e POSTGRES_USER=t -e POSTGRES_PASSWORD=t \\
        -e POSTGRES_DB=t -p 127.0.0.1:55432:5432 postgres:17-alpine
    TEST_POSTGRES_URL=postgresql+psycopg://t:t@127.0.0.1:55432/t \\
        .venv/bin/pytest tests/test_generation_lock_postgres.py

Advisory locks are per database, not per schema: use a throwaway server,
never the stack's database (a test there would contend with, or terminate,
the real worker's lock session).

The 2026-10-25 finding these guard: the lock was taken by a statement
SQLAlchemy had wrapped in a transaction that was never committed, leaving
the worker's lock session `idle in transaction` for the hours a shard took.
"""

import asyncio
import os
import sys
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.research import continuity
from app.research.continuity import (
    _ADVISORY_LOCK_KEY,
    ContinuityConfig,
    DayDecision,
    GenerationFailed,
    GenerationLock,
    GenerationLockLost,
    require_lock_owner,
    run_generation_subprocess,
    run_pass,
    shard_name,
)

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")

DAY_START = datetime(2026, 10, 20, tzinfo=UTC)
NOW = DAY_START + timedelta(days=1, hours=12)


def _engine(pool_size: int = 2) -> AsyncEngine:
    assert URL is not None
    return create_async_engine(URL, pool_pre_ping=True, pool_size=pool_size, max_overflow=0)


@pytest_asyncio.fixture
async def admin() -> AsyncGenerator[AsyncEngine]:
    """Inspects and terminates sessions; fails the test if a lock leaked."""
    engine = _engine()
    yield engine
    holders = await _holders(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
    await engine.dispose()
    assert holders == [], f"advisory lock leaked to backends {holders}"


async def _holders(admin: AsyncEngine) -> list[int]:
    async with admin.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND classid::bigint = :hi AND objid::bigint = :lo AND objsubid = 1"
            ),
            {"hi": _ADVISORY_LOCK_KEY >> 32, "lo": _ADVISORY_LOCK_KEY & 0xFFFFFFFF},
        )
        return [row[0] for row in rows]


async def _session_state(admin: AsyncEngine, pid: int) -> dict:
    async with admin.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT state, xact_start, backend_xmin::text AS backend_xmin "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": pid},
            )
        ).one()
        return dict(row._mapping)


async def _terminate(admin: AsyncEngine, pid: int) -> None:
    async with admin.begin() as conn:
        await conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    for _ in range(50):  # until Postgres has really ended the session
        if pid not in await _holders(admin):
            return
        await asyncio.sleep(0.05)


async def _pooled_backend(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one())


def _config(tmp_path: Path) -> ContinuityConfig:
    return ContinuityConfig(
        data_dir=tmp_path,
        shards_start=DAY_START,
        partition_retention_days=30,
        oi_funding_retention_days=30,
    )


@pytest.fixture
def one_due_day(monkeypatch):
    """Data readiness is not under test here: every completed day is due
    unless its shard exists (exactly like the real decision)."""

    async def decide(session, config, day, now, state, **kwargs):
        detail = {"full_feature_deadline": "2026-12-01T00:00:00+00:00"}
        if (config.data_dir / shard_name(day)).exists():
            return DayDecision(day, "preserved", detail)
        return DayDecision(day, "generate", detail)

    monkeypatch.setattr(continuity, "decide_day", decide)


def _publish(config: ContinuityConfig, day, content: str) -> None:
    """Publish like `generate_dataset`: write a tmp dir, rename into place,
    never overwrite."""
    out = config.data_dir / shard_name(day)
    tmp = config.data_dir / f".{out.name}.tmp-{os.getpid()}-{id(content)}"
    tmp.mkdir()
    (tmp / "manifest.json").write_text(content)
    if out.exists():
        raise FileExistsError(out)
    tmp.rename(out)


# -- acquisition -------------------------------------------------------------------------------


async def test_only_one_worker_acquires_and_other_keys_stay_independent(admin):
    a, b = _engine(), _engine()
    first, second = GenerationLock(a), GenerationLock(b)
    try:
        assert await first.acquire() is True
        assert await second.acquire() is False
        assert await _holders(admin) == [first.backend_pid]
        async with b.connect() as conn:  # a different key is unaffected
            other = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": 4242})
            assert other.scalar() is True
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 4242})
            await conn.commit()
    finally:
        await first.release()
        await second.release()
        await a.dispose()
        await b.dispose()


async def test_ownership_needs_no_open_transaction(admin):
    """While held (a shard takes hours) the lock session is `idle`, never
    `idle in transaction`, holds no snapshot, and survives other work."""
    engine = _engine()
    lock = GenerationLock(engine)
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        assert pid is not None
        idle = {"state": "idle", "xact_start": None, "backend_xmin": None}
        assert await _session_state(admin, pid) == idle  # straight after acquiring
        for _ in range(3):  # simulated generation: ownership checks + other queries
            assert await lock.held()
            assert await _session_state(admin, pid) == idle
            async with async_sessionmaker(bind=engine)() as session:
                await session.execute(text("SELECT count(*) FROM pg_class"))
                await session.commit()
            state = await _session_state(admin, pid)
            assert state == {"state": "idle", "xact_start": None, "backend_xmin": None}
        assert await _holders(admin) == [pid]
    finally:
        await lock.release()
        await engine.dispose()


async def test_the_previous_implementation_left_the_lock_session_idle_in_transaction(admin):
    """The bug, reproduced: `engine.connect()` autobegins a transaction and
    the lock statement was never committed."""
    engine = _engine()
    conn = await engine.connect()
    try:
        await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        pid = int((await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        state = await _session_state(admin, pid)
        assert state["state"] == "idle in transaction" and state["xact_start"] is not None
    finally:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        await conn.close()
        await engine.dispose()


# -- release -----------------------------------------------------------------------------------


async def test_release_unlocks_and_the_pooled_connection_holds_nothing(admin):
    engine, other_engine = _engine(pool_size=1), _engine()
    lock, other = GenerationLock(engine), GenerationLock(other_engine)
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        await lock.release()
        assert await _holders(admin) == []
        # The same backend went back to the pool — without the lock.
        assert await _pooled_backend(engine) == pid
        assert await other.acquire()  # anyone can take it now
    finally:
        await other.release()
        await engine.dispose()
        await other_engine.dispose()


async def test_an_unconfirmed_release_never_returns_the_connection_to_the_pool(admin):
    engine = _engine(pool_size=1)
    lock = GenerationLock(engine)
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        # Something unlocked it behind the lock's back: release cannot
        # confirm, so the connection is discarded rather than pooled.
        assert lock._conn is not None
        await lock._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        await lock._conn.commit()
        await lock.release()
        assert await _pooled_backend(engine) != pid
        assert await _holders(admin) == []
    finally:
        await engine.dispose()


async def test_a_pass_releases_the_lock_after_success_and_after_failure(
    admin, tmp_path, one_due_day
):
    engine = _engine()
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    config = _config(tmp_path)
    try:

        async def failing(day, detail):
            raise GenerationFailed("generation", "boom")

        summary = await run_pass(factory, GenerationLock(engine), config, failing, now=lambda: NOW)
        assert summary is not None and summary["failed"] == ["2026-10-20"]
        assert await _holders(admin) == []

        async def backup_crashes():
            raise RuntimeError("backup crashed")

        async def generating(day, detail):
            _publish(config, day, "ok")

        with pytest.raises(RuntimeError, match="backup crashed"):
            await run_pass(
                factory,
                GenerationLock(engine),
                config,
                generating,
                now=lambda: NOW + timedelta(days=1),  # past the failed day's backoff
                after_generation=backup_crashes,
            )
        assert (tmp_path / shard_name(DAY_START.date())).exists()
        assert await _holders(admin) == []
    finally:
        await engine.dispose()


# -- session loss -----------------------------------------------------------------------------


async def test_session_loss_releases_the_lock_and_is_detected(admin):
    a, b = _engine(pool_size=1), _engine()
    lock, other = GenerationLock(a), GenerationLock(b)
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        assert pid is not None
        await _terminate(admin, pid)
        assert await lock.held() is False
        assert await other.acquire() is True  # Postgres released it with the session
        await lock.release()  # nothing to release; must not raise
        assert await _pooled_backend(a) != pid  # the dead connection was discarded
        assert await _holders(admin) == [other.backend_pid]
    finally:
        await other.release()
        await a.dispose()
        await b.dispose()


async def test_the_child_publish_check_follows_the_worker_session(admin):
    """`require_lock_owner` is what a generation subprocess runs right before
    its rename: true only while the worker's own session holds the lock."""
    worker, child, rival = _engine(), _engine(), _engine()
    child_sessions = async_sessionmaker(bind=child, expire_on_commit=False, class_=AsyncSession)
    lock, rival_lock = GenerationLock(worker), GenerationLock(rival)
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        assert pid is not None
        await require_lock_owner(child_sessions, pid)
        await _terminate(admin, pid)
        assert await rival_lock.acquire()  # someone else holds it now
        with pytest.raises(GenerationLockLost):
            await require_lock_owner(child_sessions, pid)
    finally:
        await rival_lock.release()
        await lock.release()
        for engine in (worker, child, rival):
            await engine.dispose()


async def test_losing_the_lock_terminates_a_real_child_before_it_publishes(admin, tmp_path):
    engine = _engine()
    lock = GenerationLock(engine)
    marker = tmp_path / "still-running"
    script = (
        "import time, pathlib\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        "while True:\n"
        "    p.write_text(str(time.time())); time.sleep(0.05)\n"
    )
    try:
        assert await lock.acquire()
        pid = lock.backend_pid
        assert pid is not None
        asyncio.get_running_loop().call_later(
            0.5, lambda: asyncio.ensure_future(_terminate(admin, pid))
        )
        started = time.monotonic()
        with pytest.raises(GenerationLockLost, match="nothing published"):
            await run_generation_subprocess(
                DAY_START.date(),
                {},
                _config(tmp_path),
                lock=lock,
                lock_check_interval=timedelta(seconds=0.2),
                command=[sys.executable, "-c", script],
            )
        assert time.monotonic() - started < 10
        last_write = marker.read_text()
        await asyncio.sleep(0.3)
        assert marker.read_text() == last_write  # the child is really gone
        assert not (tmp_path / shard_name(DAY_START.date())).exists()
    finally:
        await lock.release()
        await engine.dispose()


async def test_a_worker_that_lost_its_lock_mid_pass_never_overwrites_the_recovery(
    admin, tmp_path, one_due_day
):
    """Worker A's session dies mid-generation; worker B takes over and
    publishes; A's publish check fails, so B's shard is the only one and A's
    pass reports the loss. The day is not counted as failed."""
    a, b = _engine(), _engine()
    config = _config(tmp_path)
    factory_a = async_sessionmaker(bind=a, expire_on_commit=False, class_=AsyncSession)
    factory_b = async_sessionmaker(bind=b, expire_on_commit=False, class_=AsyncSession)
    lock_a = GenerationLock(a)
    b_done = asyncio.Event()

    async def generate_a(day, detail):
        assert lock_a.backend_pid is not None
        pid = lock_a.backend_pid
        await _terminate(admin, pid)  # A's lock session ends mid-generation
        summary_b = await run_pass(
            factory_b, GenerationLock(b), config, generate_b, now=lambda: NOW
        )
        assert summary_b is not None and summary_b["generated"] == ["2026-10-20"]
        b_done.set()
        await require_lock_owner(factory_a, pid)  # A's child: last check before rename
        _publish(config, day, "A")  # never reached

    async def generate_b(day, detail):
        _publish(config, day, "B")

    try:
        summary_a = await run_pass(factory_a, lock_a, config, generate_a, now=lambda: NOW)
        assert b_done.is_set()
        assert summary_a is not None
        assert summary_a["lock_lost"] is True and summary_a["failed"] == []
        shard = tmp_path / shard_name(DAY_START.date())
        assert (shard / "manifest.json").read_text() == "B"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["continuity", shard.name]
        assert await _holders(admin) == []
    finally:
        await a.dispose()
        await b.dispose()


async def test_concurrent_workers_publish_a_shard_exactly_once(admin, tmp_path, one_due_day):
    engines = [_engine(), _engine()]
    config = _config(tmp_path)
    calls: list[str] = []

    def worker(engine: AsyncEngine, name: str):
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

        async def generate(day, detail):
            calls.append(name)
            await asyncio.sleep(0.3)
            _publish(config, day, name)

        return run_pass(factory, GenerationLock(engine), config, generate, now=lambda: NOW)

    try:
        results = await asyncio.gather(worker(engines[0], "A"), worker(engines[1], "B"))
        assert len(calls) == 1
        assert sorted(r is None for r in results) == [False, True]  # one skipped: lock held
        assert (tmp_path / shard_name(DAY_START.date()) / "manifest.json").read_text() == calls[0]
        assert await _holders(admin) == []
    finally:
        for engine in engines:
            await engine.dispose()


async def test_a_restarted_worker_reacquires_a_lock_abandoned_by_a_dead_process(admin):
    """A worker process killed while holding the lock (no release): Postgres
    ends its session when the socket closes, and the restarted worker gets it."""
    script = (
        "import asyncio, os, sys\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "from app.research.continuity import GenerationLock\n"
        "async def main():\n"
        "    lock = GenerationLock(create_async_engine(sys.argv[1]))\n"
        "    assert await lock.acquire()\n"
        "    print(lock.backend_pid, flush=True)\n"
        "    await asyncio.sleep(60)\n"
        "asyncio.run(main())\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        URL or "",
        stdout=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    engine = _engine()
    lock = GenerationLock(engine)
    try:
        assert proc.stdout is not None
        dead_pid = int((await asyncio.wait_for(proc.stdout.readline(), 30)).strip())
        assert await _holders(admin) == [dead_pid]
        assert await lock.acquire() is False
        proc.kill()  # SIGKILL: no release, no clean disconnect
        await proc.wait()
        for _ in range(50):
            if await lock.acquire():
                break
            await asyncio.sleep(0.1)
        assert await _holders(admin) == [lock.backend_pid]
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await lock.release()
        await engine.dispose()
