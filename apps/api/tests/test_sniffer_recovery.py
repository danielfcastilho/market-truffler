"""F-02: a Sniffer analysis interrupted by a restart is detected and
recovered on startup — bounded, oldest first, idempotent, labelled."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_sniffer_service import _add_instrument, _finalize_complete_frame, _seed_full_hour

from app.models.frame import MarketFrame as MarketFrameRow
from app.repositories.frame_repository import FrameRepository
from app.repositories.sniffer_repository import SnifferRepository
from app.services.frame_synchronizer import FrameSynchronizer
from app.services.sniffer import RECOVERY_LOOKBACK, Sniffer

T = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
NOW = T + timedelta(hours=1)
STEP = timedelta(minutes=5)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _instrument_with_history(session_factory) -> int:
    iid = await _add_instrument(session_factory, "BTCUSDT")
    for hours in (3, 2, 1):
        await _seed_full_hour(session_factory, iid, T - timedelta(hours=hours))
    await _seed_full_hour(session_factory, iid, T)
    return iid


async def _analyzed(session_factory) -> dict[datetime, str | None]:
    out = {}
    async with session_factory() as session:
        repo = SnifferRepository(session)
        for k in range(-24, 13):
            result = await repo.get_for_frame(T + k * STEP)
            if result is not None:
                out[result.frame_time] = result.analysis
    return out


async def _bare_frame(session_factory, frame_time: datetime, *, available: int = 0) -> None:
    async with session_factory() as session:
        session.add(
            MarketFrameRow(
                frame_time=frame_time,
                expected_instrument_ids=[],
                expected_instruments=1,
                available_instruments=available,
                status="partial",
                created_at=frame_time,
                finalized_at=frame_time,
            )
        )
        await session.commit()


async def test_finalized_frames_without_results_are_recovered_oldest_first(session_factory):
    iid = await _instrument_with_history(session_factory)
    frames = [T - STEP, T, T + STEP, T + 2 * STEP]
    for frame_time in frames:
        await _finalize_complete_frame(session_factory, iid, frame_time)
    sniffer = Sniffer(session_factory)
    await sniffer.analyze_frame(T)  # analyzed live; the others were lost

    recovered = await sniffer.recover_missing(now=NOW)

    assert recovered == [T - STEP, T + STEP, T + 2 * STEP]
    assert await _analyzed(session_factory) == {
        T - STEP: "recovery",
        T: "live",
        T + STEP: "recovery",
        T + 2 * STEP: "recovery",
    }
    # Idempotent: a second startup finds nothing left.
    assert await sniffer.recover_missing(now=NOW) == []


async def test_recovery_is_bounded_by_limit_and_lookback(session_factory):
    iid = await _instrument_with_history(session_factory)
    old = NOW - RECOVERY_LOOKBACK - STEP
    await _bare_frame(session_factory, old, available=1)
    for frame_time in (T, T + STEP, T + 2 * STEP):
        await _finalize_complete_frame(session_factory, iid, frame_time)

    recovered = await Sniffer(session_factory).recover_missing(now=NOW, limit=2)

    assert recovered == [T, T + STEP]  # never the frame beyond the lookback
    async with session_factory() as session:
        assert await SnifferRepository(session).get_for_frame(old) is None


async def test_building_and_memberless_frames_are_not_unanalyzed(session_factory):
    iid = await _instrument_with_history(session_factory)
    await _finalize_complete_frame(session_factory, iid, T)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            T + STEP, expected_instrument_ids=[iid], now=datetime.now(UTC)
        )
    await _bare_frame(session_factory, T + 2 * STEP)

    async with session_factory() as session:
        repo = SnifferRepository(session)
        assert await repo.find_unanalyzed_frames(T - STEP, NOW, limit=10) == [T]
        assert await repo.count_unanalyzed_frames(T - STEP, NOW) == 1


async def test_one_failing_frame_does_not_stop_recovery(session_factory, monkeypatch):
    iid = await _instrument_with_history(session_factory)
    for frame_time in (T, T + STEP):
        await _finalize_complete_frame(session_factory, iid, frame_time)
    sniffer = Sniffer(session_factory)
    real = sniffer.analyze_frame

    async def flaky(frame_time, **kwargs):
        if frame_time == T:
            raise RuntimeError("boom")
        await real(frame_time, **kwargs)

    monkeypatch.setattr(sniffer, "analyze_frame", flaky)

    assert await sniffer.recover_missing(now=NOW) == [T + STEP]


async def test_an_interrupted_build_is_finalized_on_startup_and_analyzed_by_recovery(
    session_factory,
):
    """Restart mid-build: startup finalizes the frame without analyzing it
    inline (startup never waits on an analysis); recovery then analyzes it."""
    iid = await _instrument_with_history(session_factory)
    async with session_factory() as session:
        await FrameRepository(session).create_building(
            T, expected_instrument_ids=[iid], now=datetime.now(UTC)
        )
    calls: list[datetime] = []

    async def hook(frame_time: datetime) -> None:
        calls.append(frame_time)

    sync = FrameSynchronizer(session_factory, on_frame_finalized=hook)
    await sync._resolve_interrupted_build()
    assert calls == []

    assert await Sniffer(session_factory).recover_missing(now=NOW) == [T]
    assert (await _analyzed(session_factory))[T] == "recovery"


async def test_startup_recovery_runs_in_the_background_and_stops_cleanly(
    session_factory, monkeypatch
):
    sniffer = Sniffer(session_factory)
    seen: list[datetime] = []

    async def fake_recover(*, now):
        seen.append(now)
        return [T]

    monkeypatch.setattr(sniffer, "recover_missing", fake_recover)
    await sniffer.start_recovery()
    assert sniffer._recovery_task is not None
    assert await sniffer._recovery_task == [T]
    assert len(seen) == 1  # once, against the startup instant
    await sniffer.stop()
    assert sniffer._recovery_task is None


async def test_vitals_reports_frames_that_were_never_analyzed(client, test_user, session_factory):
    iid = await _instrument_with_history(session_factory)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    recent = now - timedelta(minutes=now.minute % 5) - timedelta(minutes=30)
    for frame_time in (recent, recent + STEP):
        await _finalize_complete_frame(session_factory, iid, frame_time)
    await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )

    status = (await client.get("/api/sniffer/status")).json()
    assert status["frames_unanalyzed_24h"] == 2 and status["status"] is None

    await Sniffer(session_factory).analyze_frame(recent + STEP)
    status = (await client.get("/api/sniffer/status")).json()
    assert status["frames_unanalyzed_24h"] == 1
    assert status["last_analysis"] == "live"
