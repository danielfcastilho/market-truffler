"""OINK CORP research data continuity: turn every completed UTC day of
Sniffer frames into a verified, immutable research dataset shard —
automatically, preservation first.

One shard per UTC day, sampled on a fixed 15-minute grid, named
`shard_s15m_YYYYMMDD`, covering `[max(day start, SHARDS_START), day end)`.
A pass (see `run_pass`) walks every completed day since
`research_shards_start` and, under a Postgres advisory lock:

- skips days already preserved (a directory under its real name has
  always passed verification — see `app.research.dataset.generate_dataset`);
- records **collection gaps** — days with no Sniffer frame on the grid —
  and never turns them into datasets (frame headers are never pruned and
  frames are never backfilled, so "no frames" is permanent and needs no
  stored state);
- records **source-expired** days (frames exist but their members were
  pruned by retention — nothing left to replay);
- waits for label maturity, then for the **readiness gate** (candle and
  Open Interest reconciliation caught up for >= 99% of the day's still-
  active members), but only until a **bounded fallback**:
  `min(matured + 24h, full-feature deadline - 3 days)`. Past it, whatever
  is available is preserved and the shortfall is recorded in the manifest
  (recoverable-but-missing vs. permanently unavailable);
- generates the rest in deadline order, each in a **subprocess** with a
  hard timeout (killed on expiry; its database statements are bounded by
  `statement_timeout`); a failure is recorded — with its kind (`timeout`,
  `killed`, `integrity`, `generation`) — with backoff and the pass moves on,
  so one failing day never blocks newer days. A worker *stop* is not a
  failure of the day: the child is terminated, the interruption recorded
  separately, no attempt is counted and the pass ends there (the next
  pass after the restart resumes the same day from scratch).

Interrupted generation never publishes anything: a shard is assembled in
memory and written to a `.<name>.tmp-*` directory, verified there, and only
then renamed into place (`app.research.dataset.generate_dataset`); leftovers
are removed under the lock at the start of every pass. Passes run one after
another (the next waits for the previous to finish, however long a shard
takes) and the advisory lock makes them exclusive across processes.

Deadlines are derived from what replay actually reads and how each source
is pruned (`source_deadlines`), conservatively. Missing a full-feature
deadline never stops generation — whatever remains is preserved.

Independent backup is a separate step (`app.research.backup`), run after
generation under the same lock; `build_status` reports its last verified
state, never more.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.domain.timeframes import TIMEFRAMES, floor_to_boundary
from app.features.engine import FEATURES, REQUIRED_WARMUP
from app.features.funding_rate import FundingRate24hAvgFeature, FundingRateCurrentFeature
from app.features.open_interest_change import OpenInterestChangeFeature
from app.models.frame import MarketFrame, MarketFrameMember
from app.models.instrument import Instrument
from app.models.open_interest import OpenInterestObservation
from app.research.dataset import aligned_candidate_times, file_sha256, read_dataset

logger = logging.getLogger(__name__)

SHARD_PREFIX = "shard_s15m_"
SHARD_INTERVAL_MINUTES = 15
SHARD_HORIZONS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
}
LABEL_MATURITY = max(SHARD_HORIZONS.values()) + timedelta(hours=1)
READINESS_FRACTION = 0.99
#: Fraction of an instrument's expected 5-minute Open Interest buckets over
#: the replay window that must be stored before its OI counts as caught up
#: — so an interior hole (e.g. one sealed by a past outage) blocks the gate
#: even when the latest observation is recent. Tolerates Bybit's own
#: occasional missing bucket.
OI_BUCKET_COMPLETENESS = 0.99
MAX_READINESS_WAIT = timedelta(hours=24)
DEADLINE_SAFETY = timedelta(days=3)
EXPIRY_WARNING = timedelta(days=5)
BACKOFF = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
    timedelta(hours=12),
)
#: After the worker (re)starts, the readiness fallback is not honoured for
#: this long: after an outage the fallback time has usually passed already
#: while the reconcilers are still catching up — generating immediately
#: would preserve gaps that minutes later would have been filled.
RESTART_GRACE = timedelta(hours=1)
#: Consecutive failures after which a day needs a human (it keeps being
#: retried every 12h regardless).
BLOCKED_AFTER_ATTEMPTS = len(BACKOFF)
#: Hard wall-clock limit for one shard's generation subprocess. A daily
#: shard (96 frames at ~30s plus yielding) takes ~2.5-3h; 6h leaves 2x headroom.
SHARD_TIMEOUT = timedelta(hours=6)
#: Server-side cap on any single statement the generator issues, so a
#: killed generator can't leave a runaway query behind for long.
STATEMENT_TIMEOUT = timedelta(minutes=10)

# Best-effort yield to live Sniffer work: only start replaying a frame
# when the last 5-minute boundary is at least this far behind and the
# next at least this far ahead. Live finalization + Sniffer analysis
# measured ~30-40s; a frame replay averaged ~32s but one ran ~58s and
# delayed a live frame by 12s with a 45s margin (acceptance run,
# 2026-10-12), hence 75s. Not a guarantee — a slow live cycle or a slow
# replay can still overlap.
YIELD_AFTER_BOUNDARY = timedelta(seconds=120)
YIELD_BEFORE_BOUNDARY = timedelta(seconds=75)
_FIVE_MINUTES = timedelta(minutes=5)

_ADVISORY_LOCK_KEY = 0x0A1C_0C0F  # "OINK CORP research generation"
CONTINUITY_DIRNAME = "continuity"

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# -- shards ------------------------------------------------------------------------------


def shard_name(day: date) -> str:
    return f"{SHARD_PREFIX}{day:%Y%m%d}"


def day_window(day: date, shards_start: datetime) -> tuple[datetime, datetime] | None:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    start = max(start, shards_start)
    return (start, end) if start < end else None


def completed_days(shards_start: datetime, now: datetime) -> list[date]:
    """Every UTC day from the shards start whose window has fully ended."""
    days = []
    day = shards_start.date()
    while datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC) <= now:
        days.append(day)
        day += timedelta(days=1)
    return days


# -- source expiration ---------------------------------------------------------------------

#: Oldest candle any feature's replay can read, relative to the frame.
#: Every candle-window feature (RSI/volatility/relative volume) reads 15
#: candles ending at its frame's anchor, and the anchor trails the frame by
#: up to two candle durations — so the real reach is
#: `required_history + 2 x duration`, bounded by REQUIRED_WARMUP plus two of
#: the longest timeframe (17 days today). Returns reach `lookback + 10m`.
CANDLE_LOOKBACK = REQUIRED_WARMUP + 2 * max(TIMEFRAMES.values())
#: Oldest Open Interest any feature reads: the longest OI lookback from
#: the latest observation at/before the frame (plus a margin for that
#: anchor trailing the frame).
OI_LOOKBACK = max(
    (f.required_history for f in FEATURES if isinstance(f, OpenInterestChangeFeature)),
    default=timedelta(0),
) + timedelta(hours=1)
#: Oldest funding any feature reads: the 24h average window, or the latest
#: settlement at/before the frame (Bybit settles at least every 8h).
FUNDING_LOOKBACK = max(
    (
        f.required_history
        for f in FEATURES
        if isinstance(f, FundingRateCurrentFeature | FundingRate24hAvgFeature)
    ),
    default=timedelta(0),
) + timedelta(hours=8)


def _next_month_start(dt: datetime) -> datetime:
    first = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def source_deadlines(
    frame_time: datetime, *, partition_retention_days: int, oi_funding_retention_days: int
) -> dict[str, datetime]:
    """When each source a replay of `frame_time` reads starts disappearing,
    per the repository's pruning rules (assuming pruning runs promptly):

    - monthly partitions (`partition_manager.drop_expired_partitions`):
      month M is dropped once `now >= next_month_start(M) + R` — frame
      members (the whole frame) and the candle lookback (not protected by
      frame references);
    - OI / funding: rolling row deletion at `now - R_oi`.
    """
    partition = timedelta(days=partition_retention_days)
    rolling = timedelta(days=oi_funding_retention_days)
    return {
        "frame_members": _next_month_start(frame_time) + partition,
        "candle_lookback": _next_month_start(frame_time - CANDLE_LOOKBACK) + partition,
        "open_interest": frame_time - OI_LOOKBACK + rolling,
        "funding": frame_time - FUNDING_LOOKBACK + rolling,
    }


# -- configuration and state ------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuityConfig:
    data_dir: Path
    shards_start: datetime
    partition_retention_days: int
    oi_funding_retention_days: int
    pass_interval_seconds: float = 1800.0

    @property
    def continuity_dir(self) -> Path:
        return self.data_dir / CONTINUITY_DIRNAME

    def deadlines(self, frame_time: datetime) -> dict[str, datetime]:
        return source_deadlines(
            frame_time,
            partition_retention_days=self.partition_retention_days,
            oi_funding_retention_days=self.oi_funding_retention_days,
        )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    tmp.replace(path)


def load_state(config: ContinuityConfig) -> dict[str, Any]:
    """Operational retry state (not research data). Losing or corrupting it
    only forgets backoff history — every other fact is re-derived."""
    path = config.continuity_dir / "state.json"
    try:
        state = json.loads(path.read_text())
        if isinstance(state, dict) and isinstance(state.get("days"), dict):
            return state
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("continuity_state_unreadable_starting_fresh")
    return {"days": {}}


def save_state(config: ContinuityConfig, state: dict[str, Any]) -> None:
    _write_json_atomic(config.continuity_dir / "state.json", state)


def _record_failure(state: dict[str, Any], day: date, now: datetime, kind: str, error: str) -> None:
    entry = state["days"].setdefault(day.isoformat(), {"attempts": 0})
    entry["attempts"] += 1
    backoff = BACKOFF[min(entry["attempts"], len(BACKOFF)) - 1]
    entry["last_error_kind"] = kind
    entry["last_error"] = error[-2000:]
    entry["last_failure_at"] = now.isoformat()
    entry["next_retry_at"] = (now + backoff).isoformat()


def _in_backoff(state: dict[str, Any], day: date, now: datetime) -> bool:
    entry = state["days"].get(day.isoformat())
    return bool(entry and datetime.fromisoformat(entry["next_retry_at"]) > now)


# -- locking -----------------------------------------------------------------------------------


class GenerationLock:
    """Exclusive research-generation lock: a Postgres session-level advisory
    lock held on a dedicated connection — works across containers, and is
    released by Postgres itself if the holding process dies. On a non-
    Postgres database (tests) it's a no-op that always succeeds."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._conn: AsyncConnection | None = None

    async def acquire(self) -> bool:
        if self._engine.dialect.name != "postgresql":
            return True
        conn = await self._engine.connect()
        acquired = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        ).scalar()
        if not acquired:
            await conn.close()
            return False
        self._conn = conn
        return True

    async def release(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY}
            )
        finally:
            await self._conn.close()
            self._conn = None


# -- readiness -------------------------------------------------------------------------------


@dataclass(frozen=True)
class DayFacts:
    grid_frames: list[datetime]
    frames_with_members: int
    members: int
    active_members: int
    candles_ready: int
    oi_ready: int

    @property
    def gate_passed(self) -> bool:
        if self.active_members == 0:
            return True  # nothing left that could still catch up
        return (
            self.candles_ready >= READINESS_FRACTION * self.active_members
            and self.oi_ready >= READINESS_FRACTION * self.active_members
        )

    def as_manifest(self) -> dict[str, Any]:
        return {
            "frames_on_grid": len(self.grid_frames),
            "member_instruments": self.members,
            "inactive_member_instruments": self.members - self.active_members,
            "candles_lagging": self.active_members - self.candles_ready,
            "open_interest_lagging": self.active_members - self.oi_ready,
            "gate_passed": self.gate_passed,
        }


async def day_facts(session: AsyncSession, start: datetime, end: datetime) -> DayFacts:
    """Everything a pass needs to decide about one day, from the database:
    which grid frames exist, whether their members survive retention, and
    whether candle/OI reconciliation has caught up for the day's members
    (only instruments still active can still catch up)."""
    grid = set(aligned_candidate_times(start, end, SHARD_INTERVAL_MINUTES))
    frame_times = (
        await session.execute(
            select(MarketFrame.frame_time).where(
                MarketFrame.frame_time >= start, MarketFrame.frame_time < end
            )
        )
    ).scalars()
    grid_frames = sorted(t for t in frame_times if t in grid)
    if not grid_frames:
        return DayFacts([], 0, 0, 0, 0, 0)

    frames_with_members = (
        await session.execute(
            select(func.count(func.distinct(MarketFrameMember.frame_time))).where(
                MarketFrameMember.frame_time.in_(grid_frames)
            )
        )
    ).scalar_one()
    member_ids = list(
        (
            await session.execute(
                select(MarketFrameMember.instrument_id)
                .where(MarketFrameMember.frame_time.in_(grid_frames))
                .distinct()
            )
        ).scalars()
    )
    instruments = (
        (await session.execute(select(Instrument).where(Instrument.id.in_(member_ids))))
        .scalars()
        .all()
    )
    active = [i for i in instruments if i.is_active]
    candles_needed_through = end + max(SHARD_HORIZONS.values())
    candles_ready = sum(
        1
        for i in active
        if i.history_synced_through is not None
        and i.history_synced_through >= candles_needed_through
    )
    # OI is "ready" for an instrument only when the whole window replay
    # reads — from the earliest frame's OI lookback to the day's end — is
    # (almost) fully stored, not merely when the latest observation is
    # recent: a recent maximum says nothing about interior holes.
    oi_window_start = floor_to_boundary(start - OI_LOOKBACK, _FIVE_MINUTES)
    expected_buckets = int((end - oi_window_start) / _FIVE_MINUTES)
    oi_rows = (
        await session.execute(
            select(
                OpenInterestObservation.instrument_id,
                func.count(),
                func.max(OpenInterestObservation.observed_at),
            )
            .where(
                OpenInterestObservation.instrument_id.in_([i.id for i in active]),
                OpenInterestObservation.observed_at >= oi_window_start,
                OpenInterestObservation.observed_at < end,
            )
            .group_by(OpenInterestObservation.instrument_id)
        )
    ).all()
    oi_ready = sum(
        1
        for _, count, latest in oi_rows
        if latest is not None
        and latest >= end - _FIVE_MINUTES
        and count >= OI_BUCKET_COMPLETENESS * expected_buckets
    )
    return DayFacts(
        grid_frames,
        frames_with_members,
        len(member_ids),
        len(active),
        candles_ready,
        oi_ready,
    )


# -- the pass --------------------------------------------------------------------------------


#: Exit codes of `--generate-day` (the child), besides 0 and Python's own 1.
EXIT_ALREADY_PRESERVED = 3
EXIT_INTEGRITY = 4


class GenerationFailed(RuntimeError):
    """One day's generation failed. `kind` says what was actually observed:
    `timeout` (the worker's limit expired and it terminated the child),
    `killed` (the child died from a signal the worker did not send — e.g.
    the out-of-memory killer), `integrity` (the written dataset failed
    verification and was discarded), or `generation` (any other error exit)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class GenerationInterrupted(Exception):
    """The worker was asked to stop while (or before) generating: not a
    failure of the day — nothing is counted against it."""


GenerateDay = Callable[[date, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class DayDecision:
    day: date
    status: str  # preserved | collection_gap | source_expired | waiting_maturity |
    # waiting_readiness | retry_backoff | generate
    detail: dict[str, Any]


def _full_feature_deadline(config: ContinuityConfig, start: datetime) -> datetime:
    return min(config.deadlines(start).values())


async def decide_day(
    session: AsyncSession,
    config: ContinuityConfig,
    day: date,
    now: datetime,
    state: dict,
    *,
    readiness_grace_until: datetime | None = None,
) -> DayDecision:
    window = day_window(day, config.shards_start)
    assert window is not None
    start, end = window
    deadlines = config.deadlines(start)
    full_deadline = min(deadlines.values())
    detail: dict[str, Any] = {
        "window": [start.isoformat(), end.isoformat()],
        "full_feature_deadline": full_deadline.isoformat(),
        "frame_deadline": deadlines["frame_members"].isoformat(),
    }
    if (config.data_dir / shard_name(day)).exists():
        return DayDecision(day, "preserved", detail)

    facts = await day_facts(session, start, end)
    detail["frames_on_grid"] = len(facts.grid_frames)
    if not facts.grid_frames:
        return DayDecision(day, "collection_gap", detail)
    if facts.frames_with_members == 0:
        return DayDecision(day, "source_expired", detail)

    matured_at = end + LABEL_MATURITY
    if now < matured_at:
        detail["matures_at"] = matured_at.isoformat()
        return DayDecision(day, "waiting_maturity", detail)
    previous = state["days"].get(day.isoformat())
    if previous:
        detail.update(previous)  # earlier failures stay visible while retried
    if _in_backoff(state, day, now):
        return DayDecision(day, "retry_backoff", detail)

    fallback_at = min(matured_at + MAX_READINESS_WAIT, full_deadline - DEADLINE_SAFETY)
    if readiness_grace_until is not None and full_deadline - now > 2 * RESTART_GRACE:
        fallback_at = max(fallback_at, readiness_grace_until)
    readiness = facts.as_manifest() | {
        "fallback": not facts.gate_passed,
        "fallback_at": fallback_at.isoformat(),
    }
    detail["readiness"] = readiness
    if not facts.gate_passed and now < fallback_at:
        return DayDecision(day, "waiting_readiness", detail)

    detail["sources_expired_at_generation"] = sorted(k for k, t in deadlines.items() if t <= now)
    return DayDecision(day, "generate", detail)


async def run_pass(
    session_factory: Callable[[], AsyncSession],
    lock: GenerationLock,
    config: ContinuityConfig,
    generate: GenerateDay,
    *,
    now: Now = _utcnow,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    readiness_grace_until: datetime | None = None,
    after_generation: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any] | None:
    """One idempotent pass. Returns a summary, or None if another generator
    holds the lock. `after_generation` (the backup step) runs last, still
    under the lock, unless the pass was interrupted."""
    if not await lock.acquire():
        logger.info("continuity_pass_skipped_lock_held")
        return None
    try:
        removed = clean_stale_tmp_dirs(config.data_dir)
        state = load_state(config)
        decisions = []
        async with session_factory() as session:
            for day in completed_days(config.shards_start, now()):
                decisions.append(
                    await decide_day(
                        session,
                        config,
                        day,
                        now(),
                        state,
                        readiness_grace_until=readiness_grace_until,
                    )
                )
        due = sorted(
            (d for d in decisions if d.status == "generate"),
            key=lambda d: (d.detail["full_feature_deadline"], d.day),
        )
        generated: list[str] = []
        failed: list[str] = []
        interrupted: str | None = None
        for decision in due:
            if on_status is not None:
                on_status({"generating": decision.day.isoformat(), "started_at": now()})
            try:
                await generate(decision.day, decision.detail)
                state["days"].pop(decision.day.isoformat(), None)
                state["last_success"] = {"day": decision.day.isoformat(), "at": now().isoformat()}
                generated.append(decision.day.isoformat())
            except GenerationInterrupted as exc:
                logger.info("continuity_shard_interrupted", extra={"day": decision.day.isoformat()})
                interrupted = decision.day.isoformat()
                state["last_interruption"] = {
                    "day": interrupted,
                    "at": now().isoformat(),
                    "reason": str(exc),
                }
            except GenerationFailed as exc:
                logger.warning(
                    "continuity_shard_failed",
                    extra={"day": decision.day.isoformat(), "kind": exc.kind},
                )
                _record_failure(state, decision.day, now(), exc.kind, str(exc))
                state["last_failure"] = {
                    "day": decision.day.isoformat(),
                    "at": now().isoformat(),
                    "kind": exc.kind,
                    "error": str(exc)[-2000:],
                }
                failed.append(decision.day.isoformat())
            finally:
                clean_stale_tmp_dirs(config.data_dir)
            save_state(config, state)
            if interrupted is not None:
                break  # stopping: later days are not attempted, not failed
        save_state(config, state)
        if interrupted is None and after_generation is not None:
            await after_generation()
        return {
            "generated": generated,
            "failed": failed,
            "interrupted": interrupted,
            "stale_tmp_dirs_removed": removed,
            "decisions": {d.day.isoformat(): d.status for d in decisions},
        }
    finally:
        await lock.release()


def clean_stale_tmp_dirs(data_dir: Path) -> list[str]:
    """Remove leftovers of interrupted dataset generation (`.<name>.tmp-*`).
    Only ever called while holding the generation lock, so nothing is still
    writing them."""
    removed: list[str] = []
    if not data_dir.exists():
        return removed
    for path in data_dir.iterdir():
        if path.is_dir() and path.name.startswith(".") and ".tmp-" in path.name:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
    return removed


# -- generation (in a subprocess) -----------------------------------------------------------------


async def wait_for_live_window(now: Now = _utcnow, sleep=asyncio.sleep) -> float:
    """Best-effort yield: wait until the current time is at least
    `YIELD_AFTER_BOUNDARY` past the last 5-minute boundary and at least
    `YIELD_BEFORE_BOUNDARY` before the next. Returns seconds waited."""
    waited = 0.0
    while True:
        current = now()
        boundary = floor_to_boundary(current, _FIVE_MINUTES)
        since = current - boundary
        until_next = boundary + _FIVE_MINUTES - current
        if since >= YIELD_AFTER_BOUNDARY and until_next >= YIELD_BEFORE_BOUNDARY:
            return waited
        target = boundary + YIELD_AFTER_BOUNDARY
        if since >= YIELD_AFTER_BOUNDARY:
            target += _FIVE_MINUTES
        seconds = (target - current).total_seconds()
        await sleep(seconds)
        waited += seconds


async def run_generation_subprocess(
    day: date,
    detail: dict[str, Any],
    config: ContinuityConfig,
    *,
    timeout: timedelta = SHARD_TIMEOUT,
    stop: asyncio.Event | None = None,
    command: list[str] | None = None,
) -> None:
    """Generate one shard in a child process with a hard timeout. On
    timeout or stop, the child is terminated (then killed) and *waited
    for* before returning, so the caller's lock is never released while
    generation still runs. Raises `GenerationInterrupted` on a stop (also
    when one was already requested — no child is started then) and
    `GenerationFailed` on a failure, classified by what was observed. A
    child reporting that the shard already exists is not a failure."""
    if stop is not None and stop.is_set():
        raise GenerationInterrupted("worker stopping; generation not started")
    argv = command or [
        sys.executable,
        "-m",
        "app.scripts.research_continuity",
        "--generate-day",
        day.isoformat(),
        "--data-dir",
        str(config.data_dir),
        "--detail-json",
        json.dumps(detail, default=str),
    ]
    proc = await asyncio.create_subprocess_exec(*argv, stderr=asyncio.subprocess.PIPE)
    communicate = asyncio.ensure_future(proc.communicate())
    waiters: set[asyncio.Future[Any]] = {communicate}
    stop_waiter = asyncio.ensure_future(stop.wait()) if stop is not None else None
    if stop_waiter is not None:
        waiters.add(stop_waiter)
    done, _ = await asyncio.wait(
        waiters, timeout=timeout.total_seconds(), return_when=asyncio.FIRST_COMPLETED
    )
    if stop_waiter is not None and stop_waiter not in done:
        stop_waiter.cancel()

    if communicate in done:
        _, stderr = communicate.result()
        code = proc.returncode
        tail = (stderr or b"").decode(errors="replace").strip()[-2000:]
        if code in (0, EXIT_ALREADY_PRESERVED):
            return
        if code is not None and code < 0:
            raise GenerationFailed(
                "killed",
                f"child killed by {_signal_name(-code)}, not by the worker "
                f"(out of memory?): {tail}",
            )
        if code == EXIT_INTEGRITY:
            raise GenerationFailed("integrity", f"dataset failed verification, discarded: {tail}")
        raise GenerationFailed("generation", f"exit code {code}: {tail}")

    stopped = stop_waiter is not None and stop_waiter in done
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except TimeoutError:
        proc.kill()
        await proc.wait()
    with contextlib.suppress(Exception):
        await communicate
    if stopped:
        raise GenerationInterrupted("worker stopping; child terminated, nothing published")
    raise GenerationFailed("timeout", f"generation exceeded the {timeout} limit; child terminated")


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


# -- status -----------------------------------------------------------------------------------


def _dataset_entries(config: ContinuityConfig, verify: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not config.data_dir.exists():
        return entries
    for path in sorted(config.data_dir.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or not (path / "manifest.json").exists():
            continue
        manifest = json.loads((path / "manifest.json").read_text())
        entry: dict[str, Any] = {
            "name": path.name,
            "data_sha256": manifest.get("data_sha256"),
            "sample_interval_minutes": manifest.get("sample_interval_minutes"),
            "first_frame_time": manifest.get("first_frame_time"),
            "last_frame_time": manifest.get("last_frame_time"),
            "row_count": manifest.get("row_count"),
            "readiness_fallback": (manifest.get("readiness") or {}).get("fallback"),
        }
        if verify:
            try:
                read_dataset(path)
                entry["verified_now"] = True
            except (OSError, ValueError) as exc:
                entry["verified_now"] = False
                entry["verification_error"] = str(exc)
        entries.append(entry)
    return entries


async def build_status(
    session_factory: Callable[[], AsyncSession],
    config: ContinuityConfig,
    *,
    now: datetime,
    verify: bool = False,
    worker: dict[str, Any] | None = None,
    backup: dict[str, Any] | None = None,
    registrations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The persisted report Vitals reads. `backup` is the backup step's
    own last verified report (`app.research.backup.backup_report`), or
    None when no destination is configured — never inferred here.
    `registrations` is the prospective readiness the caller computed with
    the protocol's own functions (`app.research.readiness`)."""
    state = load_state(config)
    async with session_factory() as session:
        decisions = [
            await decide_day(session, config, day, now, state)
            for day in completed_days(config.shards_start, now)
        ]
    days = []
    expiry_warnings = []
    for d in decisions:
        entry = {"day": d.day.isoformat(), "status": d.status, **d.detail}
        days.append(entry)
        if d.status not in ("preserved", "collection_gap", "source_expired"):
            deadline = datetime.fromisoformat(d.detail["full_feature_deadline"])
            if deadline - now <= EXPIRY_WARNING:
                expiry_warnings.append({"day": d.day.isoformat(), "deadline": deadline.isoformat()})
    datasets = _dataset_entries(config, verify)
    protected = set((backup or {}).get("protected", []))
    for entry in datasets:
        entry["backed_up"] = entry["name"] in protected

    def count(status: str) -> int:
        return sum(1 for d in decisions if d.status == status)

    preserved = [d.day.isoformat() for d in decisions if d.status == "preserved"]
    unresolved = [d for d in decisions if d.status != "preserved" and d.detail.get("attempts")]
    missing = [
        d.day.isoformat()
        for d in decisions
        if d.status not in ("preserved", "collection_gap", "source_expired")
    ]
    return {
        "schema": 1,
        "written_at": now.isoformat(),
        "pass_interval_seconds": config.pass_interval_seconds,
        "shards_start": config.shards_start.isoformat(),
        "worker": worker or {},
        "last_success": state.get("last_success"),
        "last_failure": state.get("last_failure"),
        "last_interruption": state.get("last_interruption"),
        "backup": backup,
        "summary": {
            "preserved_days": count("preserved"),
            "last_preserved_day": preserved[-1] if preserved else None,
            "collection_gaps": count("collection_gap"),
            "source_expired_days": count("source_expired"),
            "waiting_days": count("waiting_maturity") + count("waiting_readiness"),
            # a day with an unresolved failure, whether in backoff or due again
            "failing_days": len(unresolved),
            "blocked_days": sum(
                1 for d in unresolved if d.detail["attempts"] >= BLOCKED_AFTER_ATTEMPTS
            ),
            "due_days": count("generate"),
            "missing_days": missing,
            "unrecoverable_days": count("collection_gap") + count("source_expired"),
            "expiry_warnings": len(expiry_warnings),
            "datasets": len(datasets),
            "unprotected_datasets": sum(1 for d in datasets if not d["backed_up"]),
            "backup_configured": backup is not None,
        },
        "expiry_warnings": expiry_warnings,
        "days": days,
        "datasets": datasets,
        **({"registrations": registrations} if registrations is not None else {}),
    }


def write_status(config: ContinuityConfig, status: dict[str, Any]) -> None:
    _write_json_atomic(config.continuity_dir / "status.json", status)


def spec_refs(config: ContinuityConfig, first: date, last: date) -> dict[str, Any]:
    """Spec-ready dataset references for preserved shards in [first, last]
    (each re-verified), plus the days in range that have no shard — which
    are never emitted as datasets."""
    refs, missing = [], []
    day = first
    while day <= last:
        path = config.data_dir / shard_name(day)
        if path.exists():
            manifest, _ = read_dataset(path, columns=["frame_time"])
            refs.append({"name": path.name, "data_sha256": manifest["data_sha256"]})
        else:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    return {"datasets": refs, "days_without_shard": missing}


__all__ = [
    "ContinuityConfig",
    "GenerationFailed",
    "GenerationInterrupted",
    "GenerationLock",
    "build_status",
    "file_sha256",
    "run_generation_subprocess",
    "run_pass",
    "source_deadlines",
    "spec_refs",
    "wait_for_live_window",
]
