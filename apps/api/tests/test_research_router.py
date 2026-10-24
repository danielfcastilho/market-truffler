import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.research.continuity import ContinuityConfig, build_status, write_status
from app.routers.research import research_status_from_file

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def _write(tmp_path, *, heartbeat=NOW, summary=None, worker=None, **report):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "written_at": (NOW - timedelta(minutes=10)).isoformat(),
                "worker": {
                    "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
                    "generating": None,
                    **(worker or {}),
                },
                "summary": {
                    "preserved_days": 3,
                    "last_preserved_day": "2026-10-19",
                    "missing_days": [],
                    "waiting_days": 1,
                    "due_days": 0,
                    "failing_days": 0,
                    "blocked_days": 0,
                    "collection_gaps": 2,
                    "unrecoverable_days": 2,
                    "expiry_warnings": 0,
                    "datasets": 4,
                    "unprotected_datasets": 4,
                    "backup_configured": False,
                    **(summary or {}),
                },
                "backup": None,
                **report,
            },
            default=str,
        )
    )
    return path


def _status(path):
    return research_status_from_file(path, NOW, 1800)


def test_no_report_yet_is_unknown(tmp_path):
    status = _status(tmp_path / "missing.json")
    assert status.status == "unknown" and status.backup_status == "unknown"


def test_a_fresh_report_with_nothing_due_is_healthy_and_flags_missing_backup(tmp_path):
    status = _status(_write(tmp_path))
    assert status.status == "healthy"
    assert status.last_preserved_day == "2026-10-19"
    assert status.unprotected_datasets == 4
    assert status.backup_configured is False
    assert status.backup_status == "not_configured"


def test_a_running_worker_is_not_healthy_while_days_are_unpreserved(tmp_path):
    """Regression (2026-10-24): zero shards preserved, two days overdue,
    and Vitals said OK because the worker process was running."""
    due = _status(
        _write(
            tmp_path,
            summary={"preserved_days": 0, "last_preserved_day": None, "due_days": 2},
        )
    )
    assert due.status == "recovering" and "2 day(s) due" in due.reason
    busy = _status(
        _write(
            tmp_path,
            worker={"generating": {"generating": "2026-10-12", "started_at": NOW.isoformat()}},
        )
    )
    assert busy.status == "recovering" and busy.generating_day == "2026-10-12"


def test_failures_degrade_and_repeated_failures_block(tmp_path):
    failure = {"day": "2026-10-12", "at": NOW.isoformat(), "kind": "timeout", "error": "x"}
    failing = _status(_write(tmp_path, summary={"failing_days": 1}, last_failure=failure))
    assert failing.status == "degraded" and "timeout" in failing.reason
    assert failing.last_failure.kind == "timeout"
    assert _status(_write(tmp_path, summary={"expiry_warnings": 2})).status == "degraded"
    blocked = _status(_write(tmp_path, summary={"failing_days": 1, "blocked_days": 1}))
    assert blocked.status == "blocked"
    expiring = _status(_write(tmp_path, summary={"failing_days": 1, "expiry_warnings": 1}))
    assert expiring.status == "blocked"


def test_a_crashed_pass_is_degraded(tmp_path):
    worker = {
        "last_pass_finished_at": (NOW - timedelta(hours=1)).isoformat(),
        "last_pass_error": {"at": NOW.isoformat(), "error": "OperationalError"},
    }
    assert _status(_write(tmp_path, worker=worker)).status == "degraded"


def test_a_stale_heartbeat_or_a_stopped_worker_is_unknown_never_healthy(tmp_path):
    stale = _status(_write(tmp_path, heartbeat=NOW - timedelta(minutes=10)))
    assert stale.status == "unknown"
    stopped = _status(_write(tmp_path, worker={"stopped_at": NOW.isoformat()}))
    assert stopped.status == "unknown"


def test_a_legacy_report_without_heartbeat_uses_report_age(tmp_path):
    assert _status(_write(tmp_path, heartbeat=None)).status == "healthy"
    path = _write(tmp_path, heartbeat=None)
    report = json.loads(path.read_text())
    report["written_at"] = (NOW - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(report))
    assert _status(path).status == "unknown"


def test_backup_protection_is_only_what_the_last_verified_run_says(tmp_path):
    ok = {"checked_at": NOW.isoformat(), "error": None, "errors": {}, "protected": ["a"]}
    protected = _status(_write(tmp_path, summary={"unprotected_datasets": 0}, backup=ok))
    assert protected.backup_status == "protected" and protected.backup_configured
    partial = _status(_write(tmp_path, summary={"unprotected_datasets": 1}, backup=ok))
    assert partial.backup_status == "unprotected"
    unmounted = {**ok, "error": "backup destination /x does not exist (not mounted?)"}
    failing = _status(_write(tmp_path, summary={"unprotected_datasets": 0}, backup=unmounted))
    assert failing.backup_status == "failing" and "not mounted" in failing.backup_error
    corrupt = {**ok, "errors": {"a": "differs"}}
    assert _status(_write(tmp_path, backup=corrupt)).backup_status == "failing"
    # Backup state never changes continuity health.
    assert failing.status == "healthy"


async def test_the_router_reads_exactly_what_the_worker_writes(engine, tmp_path):
    """Contract: the worker's own `build_status` output parses cleanly."""
    sf = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    config = ContinuityConfig(
        data_dir=tmp_path,
        shards_start=NOW - timedelta(days=2),
        partition_retention_days=30,
        oi_funding_retention_days=30,
    )
    write_status(config, await build_status(sf, config, now=NOW))
    status = research_status_from_file(tmp_path / "continuity" / "status.json", NOW, 1800)
    assert status.status == "healthy"  # a fresh report without a worker heartbeat
    assert status.collection_gaps == 2  # Oct 18 (from 12:00) and Oct 19: no frames
    assert status.unrecoverable_days == 2 and status.missing_days == []
    assert status.unprotected_datasets == 0 and status.backup_status == "not_configured"


async def test_research_status_requires_authentication(client):
    response = await client.get("/api/research/status")
    assert response.status_code == 401


async def test_research_status_endpoint_without_a_report(client, test_user, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "research_data_dir", str(tmp_path))
    response = await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    assert response.status_code == 200
    response = await client.get("/api/research/status")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
