"""OINK CORP research data continuity: the worker and its operator commands
(see `app.research.continuity`).

    --loop                  run passes forever (the `research` compose service)
    --once                  run one pass now and exit
    --status [--json] [--verify-all]
                            what is preserved, missing, waiting, failing, expiring,
                            and not backed up
    --spec-refs FIRST LAST  spec-ready `datasets` entries for preserved shards
                            (YYYY-MM-DD, inclusive); gaps are listed, never emitted
    --generate-day DAY      internal: generate one shard (run by the worker in a
                            subprocess, under the worker's lock)
    --backup [--verify-all] back up research records now (re-verifying every copy
                            with --verify-all), under the generation lock
    --restore NAME          restore one record (`<dataset>` or
                            `runs/<experiment>/<run>`) from the verified backup

The worker never runs migrations: it waits until the database's Alembic
revision equals this image's head (the api container migrates on start).
Backup runs after each pass only when `RESEARCH_BACKUP_DIR` is set (see
`app.research.backup`); otherwise every record is reported NOT backed up.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.session import _to_async_url, get_engine, get_session_factory
from app.research.backup import backup_report, restore_item, run_backup
from app.research.continuity import (
    EXIT_ALREADY_PRESERVED,
    EXIT_INTEGRITY,
    RESTART_GRACE,
    SHARD_HORIZONS,
    SHARD_INTERVAL_MINUTES,
    STATEMENT_TIMEOUT,
    ContinuityConfig,
    GenerationLock,
    build_status,
    day_window,
    run_generation_subprocess,
    run_pass,
    shard_name,
    spec_refs,
    wait_for_live_window,
    write_status,
)
from app.research.dataset import DatasetIntegrityError, generate_dataset
from app.research.readiness import registration_readiness

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

_API_DIR = Path(__file__).resolve().parents[2]
_HEARTBEAT = timedelta(seconds=60)


def _config(data_dir: Path | None) -> ContinuityConfig:
    settings = get_settings()
    return ContinuityConfig(
        data_dir=data_dir or Path(settings.research_data_dir),
        shards_start=settings.research_shards_start,
        partition_retention_days=settings.market_history_retention_days,
        oi_funding_retention_days=settings.market_oi_funding_retention_days,
        pass_interval_seconds=settings.research_pass_interval_seconds,
    )


def _code_alembic_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_API_DIR / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


async def _schema_matches() -> bool:
    try:
        async with get_engine().connect() as conn:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:  # noqa: BLE001 - not migrated yet / database not up yet
        return False
    return current == _code_alembic_head()


def _backup_dir() -> Path | None:
    configured = get_settings().research_backup_dir
    return Path(configured) if configured else None


def _backup_now(config: ContinuityConfig, *, reverify_all: bool = False) -> dict[str, Any] | None:
    destination = _backup_dir()
    if destination is None:
        return None
    return run_backup(config.data_dir, destination, reverify_all=reverify_all)


def _registrations(config: ContinuityConfig, now: datetime) -> list[dict[str, Any]]:
    """Prospective readiness for the report (`app.research.readiness`); a
    failure is reported, never allowed to stop the pass."""
    settings = get_settings()
    try:
        return registration_readiness(
            config.data_dir,
            campaigns_dir=Path(settings.research_campaigns_dir),
            experiments_dir=Path(settings.research_experiments_dir),
            ranking_dir=Path(settings.research_ranking_dir),
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - visibility only
        logger.exception("registration_readiness_failed")
        return [
            {
                "registration_id": None,
                "verification": "error",
                "problems": [str(exc)],
                "partitions": [],
            }
        ]


async def _status(
    config: ContinuityConfig, worker: dict[str, Any], *, verify: bool = False
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return await build_status(
        get_session_factory(),
        config,
        now=now,
        verify=verify,
        worker=worker,
        backup=backup_report(config.data_dir, configured=_backup_dir() is not None),
        registrations=_registrations(config, now),
    )


def _read_worker_field(config: ContinuityConfig) -> dict[str, Any]:
    try:
        return json.loads((config.continuity_dir / "status.json").read_text()).get("worker", {})
    except (OSError, ValueError):
        return {}


def _update_worker_field(config: ContinuityConfig, worker: dict[str, Any]) -> None:
    path = config.continuity_dir / "status.json"
    try:
        status = json.loads(path.read_text())
    except (OSError, ValueError):
        status = {"schema": 1}
    status["worker"] = worker
    write_status(config, status)


async def _one_pass(
    config: ContinuityConfig,
    worker: dict[str, Any],
    stop: asyncio.Event | None,
    *,
    readiness_grace_until: datetime | None = None,
) -> None:
    session_factory = get_session_factory()

    def on_status(info: dict[str, Any]) -> None:
        worker["generating"] = {k: str(v) for k, v in info.items()}
        _update_worker_field(config, worker)

    async def generate(day: date, detail: dict[str, Any]) -> None:
        await run_generation_subprocess(day, detail, config, stop=stop)

    async def backup() -> None:
        # Hashing and copying are blocking file I/O: keep the loop (and its
        # stop signal and heartbeat) responsive.
        await asyncio.to_thread(_backup_now, config)

    worker["last_pass_started_at"] = datetime.now(UTC).isoformat()
    summary = await run_pass(
        session_factory,
        GenerationLock(get_engine()),
        config,
        generate,
        on_status=on_status,
        readiness_grace_until=readiness_grace_until,
        after_generation=backup,
    )
    worker["generating"] = None
    worker["last_pass_finished_at"] = datetime.now(UTC).isoformat()
    worker["last_pass_summary"] = (
        {"lock_held_elsewhere": True}
        if summary is None
        else {k: v for k, v in summary.items() if k != "decisions"}
    )
    write_status(config, await _status(config, worker))
    logger.info("continuity_pass_complete", extra={"summary": worker["last_pass_summary"]})


async def _loop(config: ContinuityConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    while not await _schema_matches():
        logger.info("continuity_waiting_for_migrations")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
            return
        except TimeoutError:
            pass

    started = datetime.now(UTC)
    worker: dict[str, Any] = {"started_at": started.isoformat()}

    async def heartbeat() -> None:
        # Proof of life independent of pass length (a pass may generate for
        # hours): Vitals treats a silent worker as unknown, never healthy.
        while True:
            worker["heartbeat_at"] = datetime.now(UTC).isoformat()
            _update_worker_field(config, worker)
            await asyncio.sleep(_HEARTBEAT.total_seconds())

    beating = asyncio.create_task(heartbeat())
    try:
        while not stop.is_set():
            try:
                await _one_pass(config, worker, stop, readiness_grace_until=started + RESTART_GRACE)
            except Exception as exc:  # noqa: BLE001 - one bad pass must not kill the worker
                logger.exception("continuity_pass_failed")
                worker["generating"] = None
                worker["last_pass_error"] = {
                    "at": datetime.now(UTC).isoformat(),
                    "error": repr(exc)[-2000:],
                }
                _update_worker_field(config, worker)
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.pass_interval_seconds)
            except TimeoutError:
                pass
    finally:
        beating.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beating
        worker["generating"] = None
        worker["stopped_at"] = datetime.now(UTC).isoformat()
        _update_worker_field(config, worker)
    logger.info("continuity_worker_stopped")


async def _generate_day(
    day: date, config: ContinuityConfig, detail: dict[str, Any], *, allow_immature: bool
) -> None:
    window = day_window(day, config.shards_start)
    if window is None:
        raise SystemExit(f"{day} is before the shards start {config.shards_start}")
    start, end = window
    if not allow_immature and "readiness" not in detail:
        raise SystemExit("--generate-day is run by the worker; use --allow-immature for a sandbox")
    engine = create_async_engine(
        _to_async_url(str(get_settings().database_url)),
        pool_pre_ping=True,
        connect_args={
            "options": f"-c statement_timeout={int(STATEMENT_TIMEOUT.total_seconds() * 1000)}"
        },
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        manifest = await generate_dataset(
            session_factory,
            start=start,
            end=end,
            interval_minutes=SHARD_INTERVAL_MINUTES,
            horizons=SHARD_HORIZONS,
            output_dir=config.data_dir / shard_name(day),
            before_frame=wait_for_live_window,
            extra_manifest={
                "generation_mode": "continuity",
                "shard_day": day.isoformat(),
                "readiness": detail.get("readiness"),
                "full_feature_deadline": detail.get("full_feature_deadline"),
                "frame_deadline": detail.get("frame_deadline"),
                "sources_expired_at_generation": detail.get("sources_expired_at_generation", []),
            },
        )
    finally:
        await engine.dispose()
    logger.info(
        "continuity_shard_preserved",
        extra={
            "day": day.isoformat(),
            "rows": manifest["row_count"],
            "sha256": manifest["data_sha256"],
        },
    )


def _print_status(status: dict[str, Any]) -> None:
    s = status["summary"]
    worker = status.get("worker") or {}
    print(f"shards since {status['shards_start']}  (status at {status['written_at']})")
    print(
        f"worker: last pass finished {worker.get('last_pass_finished_at')}, "
        f"generating now: {(worker.get('generating') or {}).get('generating')}"
    )
    print(f"last success: {status.get('last_success')}")
    print(f"last failure: {status.get('last_failure')}")
    print(f"last interruption: {status.get('last_interruption')}")
    print(
        f"preserved days {s['preserved_days']} (latest {s['last_preserved_day']}), "
        f"collection gaps {s['collection_gaps']}, source-expired {s['source_expired_days']}, "
        f"waiting {s['waiting_days']}, failing {s['failing_days']}, due {s['due_days']}"
    )
    for day in status["days"]:
        extra = ""
        if day.get("attempts"):
            extra = (
                f" attempts {day['attempts']}, next retry {day.get('next_retry_at')}: "
                f"[{day.get('last_error_kind', '?')}] {day.get('last_error', '')[:120]}"
            )
        elif day["status"] in ("waiting_maturity",):
            extra = f" matures {day.get('matures_at')}"
        elif day["status"] == "waiting_readiness":
            extra = f" fallback at {day['readiness']['fallback_at']}"
        print(f"  {day['day']}  {day['status']:<18} deadline {day['full_feature_deadline']}{extra}")
    for warning in status["expiry_warnings"]:
        print(f"WARNING: {warning['day']} full-feature sources expire {warning['deadline']}")
    backup = status.get("backup")
    if backup is None:
        where = "no backup destination configured"
    elif backup.get("error"):
        where = f"BACKUP FAILING: {backup['error']}"
    else:
        where = f"last verified backup {backup.get('checked_at')} to {backup.get('destination')}"
    print(
        f"datasets {s['datasets']}: all verified at write time; "
        f"NOT independently backed up: {s['unprotected_datasets']} ({where})"
    )
    for name, error in ((backup or {}).get("errors") or {}).items():
        print(f"  BACKUP ERROR {name}: {error}")
    for d in status["datasets"]:
        verified = d.get("verified_now")
        flag = "" if verified is None else ("  re-verified OK" if verified else "  VERIFY FAILED")
        print(f"  {d['name']}  rows {d['row_count']}{flag}")


async def _locked(work: Any) -> Any:
    """Run blocking file work under the generation lock, so it never
    overlaps shard generation (or another backup/restore)."""
    lock = GenerationLock(get_engine())
    if not await lock.acquire():
        raise SystemExit("research generation lock is held (generation running); try later")
    try:
        return await asyncio.to_thread(work)
    finally:
        await lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--loop", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--spec-refs", nargs=2, metavar=("FIRST", "LAST"))
    mode.add_argument("--generate-day", metavar="DAY")
    mode.add_argument("--backup", action="store_true")
    mode.add_argument("--restore", metavar="NAME")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-all", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--detail-json", default="{}")
    parser.add_argument(
        "--allow-immature",
        action="store_true",
        help="sandbox only: generate --generate-day outside the worker, into a --data-dir "
        "that is not the preserved research data directory",
    )
    args = parser.parse_args()
    config = _config(args.data_dir)

    if args.loop:
        asyncio.run(_loop(config))
    elif args.once:
        asyncio.run(_one_pass(config, _read_worker_field(config), None))
    elif args.status:
        status = asyncio.run(_status(config, _read_worker_field(config), verify=args.verify_all))
        if args.json:
            print(json.dumps(status, indent=2, default=str))
        else:
            _print_status(status)
    elif args.backup:
        if _backup_dir() is None:
            parser.error("RESEARCH_BACKUP_DIR is not set: no backup destination is configured")
        report = asyncio.run(_locked(lambda: _backup_now(config, reverify_all=args.verify_all)))
        print(json.dumps({k: v for k, v in report.items() if k != "items"}, indent=2))
        print(f"protected: {len(report['items'])}")
        sys.exit(1 if report["error"] or report["errors"] else 0)
    elif args.restore:
        destination = _backup_dir()
        if destination is None:
            parser.error("RESEARCH_BACKUP_DIR is not set: no backup destination is configured")
        restored = asyncio.run(
            _locked(lambda: restore_item(destination, config.data_dir, args.restore))
        )
        print(f"restored and verified: {restored}")
    elif args.spec_refs:
        first, last = (date.fromisoformat(v) for v in args.spec_refs)
        print(json.dumps(spec_refs(config, first, last), indent=2))
    else:
        production = Path(get_settings().research_data_dir).resolve()
        if args.allow_immature and config.data_dir.resolve() == production:
            parser.error("--allow-immature never writes into the preserved research data directory")
        try:
            asyncio.run(
                _generate_day(
                    date.fromisoformat(args.generate_day),
                    config,
                    json.loads(args.detail_json),
                    allow_immature=args.allow_immature,
                )
            )
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(EXIT_ALREADY_PRESERVED)
        except DatasetIntegrityError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(EXIT_INTEGRITY)


if __name__ == "__main__":
    main()
