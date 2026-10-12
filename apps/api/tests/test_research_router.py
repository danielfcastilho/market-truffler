import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.research.continuity import ContinuityConfig, build_status, write_status
from app.routers.research import research_status_from_file

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def _write(tmp_path, *, written_at, summary=None, generating=None):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "written_at": written_at.isoformat(),
                "worker": {"generating": generating},
                "summary": {
                    "preserved_days": 3,
                    "last_preserved_day": "2026-10-19",
                    "waiting_days": 1,
                    "failing_days": 0,
                    "collection_gaps": 2,
                    "expiry_warnings": 0,
                    "unprotected_datasets": 4,
                    "backup_configured": False,
                    **(summary or {}),
                },
            }
        )
    )
    return path


def test_no_report_yet_is_unavailable(tmp_path):
    assert research_status_from_file(tmp_path / "missing.json", NOW, 1800).status == "unavailable"


def test_a_fresh_healthy_report_is_ok_and_reports_unprotected_datasets(tmp_path):
    status = research_status_from_file(
        _write(tmp_path, written_at=NOW - timedelta(minutes=10)), NOW, 1800
    )
    assert status.status == "ok"
    assert status.last_preserved_day == "2026-10-19"
    assert status.unprotected_datasets == 4
    assert status.backup_configured is False


def test_failing_days_or_expiry_warnings_are_degraded(tmp_path):
    path = _write(tmp_path, written_at=NOW, summary={"failing_days": 1})
    assert research_status_from_file(path, NOW, 1800).status == "degraded"
    path = _write(tmp_path, written_at=NOW, summary={"expiry_warnings": 2})
    assert research_status_from_file(path, NOW, 1800).status == "degraded"


def test_a_stale_report_is_down_unless_a_shard_is_still_being_generated(tmp_path):
    stale = NOW - timedelta(hours=2)
    assert research_status_from_file(_write(tmp_path, written_at=stale), NOW, 1800).status == "down"
    busy = _write(
        tmp_path,
        written_at=stale,
        generating={
            "generating": "2026-10-19",
            "started_at": (NOW - timedelta(hours=1)).isoformat(),
        },
    )
    status = research_status_from_file(busy, NOW, 1800)
    assert status.status == "ok" and status.generating_day == "2026-10-19"


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
    assert status.status == "ok"
    assert status.collection_gaps == 2  # Oct 18 (from 12:00) and Oct 19: no frames
    assert status.unprotected_datasets == 0


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
    assert response.json()["status"] == "unavailable"
