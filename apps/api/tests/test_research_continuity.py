import asyncio
import json
import sys
import time as wallclock
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.timeframes import TIMEFRAMES
from app.features.engine import FEATURES, REQUIRED_WARMUP
from app.features.funding_rate import FundingRate24hAvgFeature, FundingRateCurrentFeature
from app.features.open_interest_change import OpenInterestChangeFeature
from app.models.frame import MarketFrameMember
from app.models.instrument import Instrument
from app.repositories.frame_repository import FrameRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.research import continuity
from app.research.continuity import (
    CANDLE_LOOKBACK,
    FUNDING_LOOKBACK,
    OI_LOOKBACK,
    ContinuityConfig,
    GenerationFailed,
    build_status,
    completed_days,
    day_window,
    run_generation_subprocess,
    run_pass,
    shard_name,
    source_deadlines,
    spec_refs,
    wait_for_live_window,
)

DAY = date(2026, 10, 14)
DAY_START = datetime(2026, 10, 14, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
MATURED = DAY_END + timedelta(hours=5)


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


def _config(tmp_path, start=DAY_START) -> ContinuityConfig:
    return ContinuityConfig(
        data_dir=tmp_path,
        shards_start=start,
        partition_retention_days=30,
        oi_funding_retention_days=30,
    )


class _Lock:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.held = False

    async def acquire(self) -> bool:
        self.held = self.available
        return self.available

    async def release(self) -> None:
        self.held = False


class _Generator:
    """Records calls; writes a placeholder shard directory like a real
    (verified) generation would, or fails for chosen days."""

    def __init__(self, config, fail_days=()):
        self.config = config
        self.fail_days = set(fail_days)
        self.calls: list[tuple[date, dict]] = []

    async def __call__(self, day, detail):
        self.calls.append((day, detail))
        if day in self.fail_days:
            raise GenerationFailed(f"boom on {day}")
        out = self.config.data_dir / shard_name(day)
        out.mkdir()
        (out / "manifest.json").write_text(json.dumps({"row_count": 1, "data_sha256": "x"}))


async def _instrument(sf, symbol, *, active=True, synced_through=None) -> int:
    async with sf() as session:
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol,
            quote_coin="USDT",
            is_active=active,
            first_seen_at=DAY_START - timedelta(days=60),
            last_seen_at=DAY_START,
            history_synced_through=synced_through or DAY_END + timedelta(hours=6),
        )
        session.add(row)
        await session.commit()
        return row.id


async def _frames(sf, ids, times):
    async with sf() as session:
        repo = FrameRepository(session)
        for t in times:
            await repo.create_building(t, expected_instrument_ids=ids, now=t)
            await repo.finalize(
                t,
                [
                    {
                        "frame_time": t,
                        "instrument_id": i,
                        "open_time_5m": t - timedelta(minutes=5),
                        "open_time_15m": t - timedelta(minutes=15),
                        "open_time_1h": t - timedelta(hours=1),
                    }
                    for i in ids
                ],
                now=t,
            )


async def _oi(sf, ids, day_end, *, skip=()):
    """Every 5-minute OI bucket replay of that day reads — from the day's
    start minus the OI lookback up to its end — except any in `skip`.
    Upserted, so overlapping days' windows can both be seeded."""
    t = day_end - timedelta(days=1) - OI_LOOKBACK
    t = t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)
    rows = []
    while t < day_end:
        if t not in skip:
            rows += [{"instrument_id": i, "observed_at": t, "open_interest": 1} for i in ids]
        t += timedelta(minutes=5)
    async with sf() as session:
        await OpenInterestRepository(session).upsert_many(rows)


async def _ready_day(sf, symbols=("A", "B"), day_start=DAY_START):
    ids = [await _instrument(sf, s) for s in symbols]
    await _frames(sf, ids, [day_start + timedelta(minutes=15 * k) for k in range(4)])
    await _oi(sf, ids, day_start + timedelta(days=1))
    return ids


# -- shard windows and deadlines --------------------------------------------------------------


def test_shard_windows_clip_to_the_start_and_only_count_completed_days():
    start = datetime(2026, 10, 12, 15, tzinfo=UTC)
    assert day_window(date(2026, 10, 12), start) == (start, datetime(2026, 10, 13, tzinfo=UTC))
    assert day_window(date(2026, 10, 11), start) is None
    now = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
    assert completed_days(start, now) == [date(2026, 10, 12), date(2026, 10, 13)]
    assert shard_name(date(2026, 10, 12)) == "shard_s15m_20261012"


def test_lookbacks_are_derived_from_real_feature_dependencies():
    """Every feature is either candle-based (bounded by REQUIRED_WARMUP plus
    two of the longest candle durations, because window anchors trail the
    frame), Open Interest, or Funding — a new kind of feature must force a
    review of the deadline model."""
    oi_or_funding = (OpenInterestChangeFeature, FundingRateCurrentFeature, FundingRate24hAvgFeature)
    longest = max(TIMEFRAMES.values())
    for feature in FEATURES:
        if isinstance(feature, oi_or_funding):
            continue
        assert feature.required_history <= REQUIRED_WARMUP
        assert getattr(feature, "_duration", timedelta(0)) <= longest
    assert CANDLE_LOOKBACK == timedelta(days=17)
    assert OI_LOOKBACK == timedelta(hours=25)
    assert FUNDING_LOOKBACK == timedelta(hours=32)


@pytest.mark.parametrize(
    ("frame_time", "candle_deadline"),
    [
        # 17 days back crosses into September: September's partition goes
        # on Oct 1 + 30d -> only ~14 days after this mid-October frame.
        (datetime(2026, 10, 17, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)),
        # 17 days back stays in October: October's partition lasts to Dec 1.
        (datetime(2026, 10, 18, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
    ],
)
def test_source_deadlines_follow_partition_and_rolling_retention(frame_time, candle_deadline):
    deadlines = source_deadlines(
        frame_time, partition_retention_days=30, oi_funding_retention_days=30
    )
    assert deadlines["candle_lookback"] == candle_deadline
    assert deadlines["frame_members"] == datetime(2026, 12, 1, tzinfo=UTC)
    assert deadlines["open_interest"] == frame_time - timedelta(hours=25) + timedelta(days=30)
    assert deadlines["funding"] == frame_time - timedelta(hours=32) + timedelta(days=30)


def test_default_oi_funding_retention_covers_replay_and_warmup():
    retention = timedelta(days=Settings.model_fields["market_oi_funding_retention_days"].default)
    assert retention >= REQUIRED_WARMUP + timedelta(days=1)
    assert retention == timedelta(days=30)


# -- the pass ----------------------------------------------------------------------------


async def test_a_ready_matured_day_is_generated_once_and_then_idempotent(session_factory, tmp_path):
    await _ready_day(session_factory)
    config = _config(tmp_path)
    gen = _Generator(config)

    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert summary["generated"] == [DAY.isoformat()]
    readiness = gen.calls[0][1]["readiness"]
    assert readiness["gate_passed"] is True and readiness["fallback"] is False

    again = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert again["generated"] == [] and len(gen.calls) == 1
    assert again["decisions"][DAY.isoformat()] == "preserved"


async def test_a_day_without_frames_is_a_collection_gap_never_a_dataset(session_factory, tmp_path):
    config = _config(tmp_path)
    gen = _Generator(config)
    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert summary["decisions"] == {DAY.isoformat(): "collection_gap"}
    assert gen.calls == [] and not (tmp_path / shard_name(DAY)).exists()

    status = await build_status(session_factory, config, now=MATURED)
    assert status["summary"]["collection_gaps"] == 1
    assert spec_refs(config, DAY, DAY) == {"datasets": [], "days_without_shard": [DAY.isoformat()]}


async def test_frames_off_the_15m_grid_do_not_count(session_factory, tmp_path):
    ids = [await _instrument(session_factory, "A")]
    await _frames(session_factory, ids, [DAY_START + timedelta(minutes=5)])
    summary = await run_pass(
        session_factory,
        _Lock(),
        _config(tmp_path),
        _Generator(_config(tmp_path)),
        now=lambda: MATURED,
    )
    assert summary["decisions"][DAY.isoformat()] == "collection_gap"


async def test_waits_for_label_maturity(session_factory, tmp_path):
    await _ready_day(session_factory)
    config = _config(tmp_path)
    gen = _Generator(config)
    early = MATURED - timedelta(minutes=1)
    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: early)
    assert summary["decisions"][DAY.isoformat()] == "waiting_maturity" and gen.calls == []


async def test_readiness_waits_but_only_until_the_bounded_fallback(session_factory, tmp_path):
    ids = [
        await _instrument(session_factory, s, synced_through=DAY_END)  # candles lag
        for s in ("A", "B")
    ]
    await _frames(session_factory, ids, [DAY_START])
    await _oi(session_factory, ids, DAY_END)
    config = _config(tmp_path)
    gen = _Generator(config)

    waiting = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert waiting["decisions"][DAY.isoformat()] == "waiting_readiness" and gen.calls == []

    fallback = MATURED + timedelta(hours=24)
    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: fallback)
    assert summary["generated"] == [DAY.isoformat()]
    readiness = gen.calls[0][1]["readiness"]
    assert readiness["fallback"] is True and readiness["candles_lagging"] == 2


async def test_late_catch_up_preserves_immediately_and_records_expired_sources(
    session_factory, tmp_path
):
    """Past the full-feature deadline (e.g. after a long laptop downtime),
    the day is still preserved — at once, without waiting for readiness —
    and the expired sources are recorded."""
    ids = [await _instrument(session_factory, s, synced_through=DAY_END) for s in ("A", "B")]
    await _frames(session_factory, ids, [DAY_START])
    config = _config(tmp_path)
    gen = _Generator(config)
    late = DAY_START + timedelta(days=29, hours=12)
    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: late)
    assert DAY.isoformat() in summary["generated"]
    detail = gen.calls[0][1]
    assert detail["readiness"]["fallback"] is True
    assert "candle_lookback" in detail["sources_expired_at_generation"]


async def test_a_recent_oi_maximum_with_an_interior_hole_does_not_pass_the_gate(
    session_factory, tmp_path
):
    """Regression: the gate used to check only the latest OI observation,
    so a hole sealed inside the replay window (e.g. by a past outage) was
    invisible and would have been frozen into the shard."""
    ids = [await _instrument(session_factory, s) for s in ("A", "B")]
    await _frames(session_factory, ids, [DAY_START])
    hole = {DAY_START - timedelta(hours=6) + timedelta(minutes=5 * k) for k in range(24)}
    await _oi(session_factory, ids, DAY_END, skip=hole)  # 2h missing, latest is recent
    config = _config(tmp_path)
    gen = _Generator(config)

    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert summary["decisions"][DAY.isoformat()] == "waiting_readiness" and gen.calls == []

    # Bounded: past the fallback the day is still preserved, the shortfall recorded.
    later = MATURED + timedelta(hours=24)
    await run_pass(session_factory, _Lock(), config, gen, now=lambda: later)
    assert gen.calls[0][1]["readiness"]["open_interest_lagging"] == 2


async def test_inactive_instruments_never_block_readiness(session_factory, tmp_path):
    active = await _instrument(session_factory, "A")
    delisted = await _instrument(session_factory, "B", active=False, synced_through=DAY_START)
    await _frames(session_factory, [active, delisted], [DAY_START])
    await _oi(session_factory, [active], DAY_END)
    config = _config(tmp_path)
    gen = _Generator(config)
    await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    readiness = gen.calls[0][1]["readiness"]
    assert readiness["gate_passed"] is True
    assert readiness["inactive_member_instruments"] == 1


async def test_a_day_whose_members_were_pruned_is_source_expired(session_factory, tmp_path):
    await _ready_day(session_factory)
    async with session_factory() as session:
        await session.execute(delete(MarketFrameMember))
        await session.commit()
    config = _config(tmp_path)
    gen = _Generator(config)
    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: MATURED)
    assert summary["decisions"][DAY.isoformat()] == "source_expired" and gen.calls == []


async def test_a_failing_day_backs_off_without_blocking_newer_days(session_factory, tmp_path):
    day2_start = DAY_START + timedelta(days=1)
    ids = await _ready_day(session_factory)
    await _frames(session_factory, ids, [day2_start])
    await _oi(session_factory, ids, day2_start + timedelta(days=1))
    for i in ids:
        async with session_factory() as session:
            row = await session.get(Instrument, i)
            row.history_synced_through = day2_start + timedelta(days=1, hours=6)
            await session.commit()
    config = _config(tmp_path)
    gen = _Generator(config, fail_days={DAY})
    now = day2_start + timedelta(days=1, hours=5)

    summary = await run_pass(session_factory, _Lock(), config, gen, now=lambda: now)
    assert summary["failed"] == [DAY.isoformat()]
    assert summary["generated"] == [(DAY + timedelta(days=1)).isoformat()]
    state = json.loads((tmp_path / "continuity" / "state.json").read_text())
    assert state["days"][DAY.isoformat()]["attempts"] == 1
    assert "boom" in state["last_failure"]["error"]

    soon = await run_pass(
        session_factory, _Lock(), config, gen, now=lambda: now + timedelta(minutes=5)
    )
    assert soon["decisions"][DAY.isoformat()] == "retry_backoff" and len(gen.calls) == 2

    gen.fail_days.clear()
    later = await run_pass(
        session_factory, _Lock(), config, gen, now=lambda: now + timedelta(hours=1)
    )
    assert later["generated"] == [DAY.isoformat()]
    state = json.loads((tmp_path / "continuity" / "state.json").read_text())
    assert DAY.isoformat() not in state["days"]


async def test_a_held_lock_skips_the_pass_entirely(session_factory, tmp_path):
    await _ready_day(session_factory)
    config = _config(tmp_path)
    gen = _Generator(config)
    assert (
        await run_pass(session_factory, _Lock(available=False), config, gen, now=lambda: MATURED)
        is None
    )
    assert gen.calls == []


async def test_stale_tmp_dirs_from_an_interrupted_generation_are_removed(session_factory, tmp_path):
    (tmp_path / ".shard_s15m_20261014.tmp-dead").mkdir()
    summary = await run_pass(
        session_factory,
        _Lock(),
        _config(tmp_path),
        _Generator(_config(tmp_path)),
        now=lambda: MATURED,
    )
    assert summary["stale_tmp_dirs_removed"] == [".shard_s15m_20261014.tmp-dead"]


async def test_a_corrupt_state_file_only_forgets_backoff(session_factory, tmp_path):
    await _ready_day(session_factory)
    (tmp_path / "continuity").mkdir()
    (tmp_path / "continuity" / "state.json").write_text("{not json")
    config = _config(tmp_path)
    summary = await run_pass(
        session_factory, _Lock(), config, _Generator(config), now=lambda: MATURED
    )
    assert summary["generated"] == [DAY.isoformat()]


async def test_status_reports_unprotected_datasets_and_expiry_warnings(session_factory, tmp_path):
    ids = [await _instrument(session_factory, s, synced_through=DAY_END) for s in ("A", "B")]
    await _frames(session_factory, ids, [DAY_START])
    config = _config(tmp_path)
    other = tmp_path / "research_first"
    other.mkdir()
    (other / "manifest.json").write_text(json.dumps({"row_count": 5}))
    # 12 days later: candle-lookback deadline (Oct 31) is < 5 days away.
    status = await build_status(session_factory, config, now=DAY_START + timedelta(days=13))
    assert status["summary"]["unprotected_datasets"] == 1
    assert status["summary"]["backup_configured"] is False
    assert status["summary"]["expiry_warnings"] == 1
    assert status["datasets"][0]["backed_up"] is False


# -- yielding and the subprocess runner ------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected_wait"),
    [
        (datetime(2026, 10, 14, 10, 2, 30, tzinfo=UTC), 0.0),  # 150s after, 150s before: go
        (datetime(2026, 10, 14, 10, 0, 30, tzinfo=UTC), 90.0),  # too soon after a boundary
        (datetime(2026, 10, 14, 10, 4, 30, tzinfo=UTC), 150.0),  # too close to the next
    ],
)
async def test_replay_yields_around_live_frame_boundaries(now, expected_wait):
    clock = [now]
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    waited = await wait_for_live_window(now=lambda: clock[0], sleep=fake_sleep)
    assert waited == expected_wait


async def test_subprocess_success_and_failure_are_reported(tmp_path):
    config = _config(tmp_path)
    await run_generation_subprocess(DAY, {}, config, command=[sys.executable, "-c", "pass"])
    with pytest.raises(GenerationFailed, match="exit code 2: nope"):
        await run_generation_subprocess(
            DAY,
            {},
            config,
            command=[sys.executable, "-c", "import sys; sys.stderr.write('nope'); sys.exit(2)"],
        )


async def test_a_timed_out_generation_is_terminated_before_returning(tmp_path):
    marker = tmp_path / "still-running"
    script = (
        "import time, pathlib\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        "while True:\n"
        "    p.write_text(str(time.time())); time.sleep(0.05)\n"
    )
    started = wallclock.monotonic()
    with pytest.raises(GenerationFailed, match="timed out"):
        await run_generation_subprocess(
            DAY,
            {},
            _config(tmp_path),
            timeout=timedelta(seconds=0.5),
            command=[sys.executable, "-c", script],
        )
    assert wallclock.monotonic() - started < 10
    last_write = marker.read_text()
    await asyncio.sleep(0.3)
    assert marker.read_text() == last_write  # the child is really gone


async def test_a_stop_request_terminates_the_child(tmp_path):
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.3, stop.set)
    with pytest.raises(GenerationFailed, match="stopped"):
        await run_generation_subprocess(
            DAY,
            {},
            _config(tmp_path),
            stop=stop,
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
        )


def test_continuity_module_never_backs_up_anywhere():
    """No backup destination is authorized: nothing in the continuity code
    copies research data elsewhere."""
    source = (continuity.__file__ and open(continuity.__file__).read()) or ""
    for forbidden in ("copytree", "shutil.copy", "rsync", "upload"):
        assert forbidden not in source
